from __future__ import annotations

from dataclasses import dataclass

import youtube_transcript_api as _yta
from youtube_transcript_api import YouTubeTranscriptApi

from src.config import AppConfig
from src.models import TranscriptResult, TranscriptSegment
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
    VideoUnavailableError,
)
from src.utils.http_identity import build_session
from src.utils.text_utils import MIN_TRANSCRIPT_CHARS, normalize_text


class _NeverRaised(Exception):
    """Hicbir zaman firlatilmayan yer tutucu.

    Bos bir `except ()` demeti sozdizimsel olarak gecerli ama hicbir seyi
    yakalamaz; bir demet DE gerekiyor. Eskiden bu yer tutucu `LookupError`
    idi ve bu tehlikeliydi: aranan istisna adi kutuphanede yoksa dal
    `KeyError`/`IndexError` yakalamaya baslar, yani gercek bir kodlama
    hatasi "altyazi bulunamadi" gibi gorunurdu.
    """


def _exc(*names: str) -> tuple[type[BaseException], ...]:
    """Kutuphane surumleri arasinda degisen istisna adlarini guvenle toplar."""
    found = tuple(
        getattr(_yta, name) for name in names if isinstance(getattr(_yta, name, None), type)
    )
    return found or (_NeverRaised,)


# Videoya ozgu, kalici durumlar -> saglayici cezalandirilmamali.
# `InvalidVideoId` BURADA: bozuk bir video kimligi saglayicinin sagligi
# hakkinda hicbir sey soylemez. Sinifsiz kaldigi surece genel hata dalina
# dusuyor ve tek bir kotu kimlik saglayiciyi cooldown'a itebiliyordu.
_VIDEO_LEVEL_ERRORS = _exc(
    "TranscriptsDisabled",
    "NoTranscriptFound",
    "VideoUnavailable",
    "VideoUnplayable",
    "NotTranslatable",
    "TranslationLanguageNotAvailable",
    "AgeRestricted",
    "InvalidVideoId",
)

# Hiz siniri / IP blogu -> tekrar denemek DURUMU KOTULESTIRIR.
# `IpBlocked`, `RequestBlocked`in alt sinifi; ikisi de ayni demette oldugu
# icin siralama onemsiz.
_RATE_LIMIT_ERRORS = _exc(
    "RequestBlocked",
    "IpBlocked",
    "TooManyRequests",
)

# Yapilandirma eksigi -> tekrar denemek de dinlendirmek de duzeltmez.
# `PoTokenRequired` YouTube'un guncel engelleme bicimi: istegin bir tarayicidan
# geldigini kanitlayan jeton isteniyor. Sinifsiz kaldigi surece genel hata
# dalina dusuyor, ucuncu videoda saglayiciyi sunucu geneli cooldown'a sokuyor
# ve kullanici ne yapmasi gerektigini hicbir yerde ogrenemiyordu.
_CONFIG_ERRORS = _exc("PoTokenRequired")

# Diger altyapi hatalari -> tekrar denenebilir.
# `YouTubeDataUnparsable` = YouTube sayfa yapisini degistirdi; gecici ve
# saglayici duzeyinde bir durum, videoya ozgu degil.
_INFRA_ERRORS = _exc(
    "YouTubeRequestFailed",
    "YouTubeDataUnparsable",
)

# Yukaridakilerin hicbirine girmeyen `CouldNotRetrieveTranscript` turevleri.
# ACIK bir dal olmasinin sebebi: kutuphane yeni bir istisna eklediginde bu
# sessizce genel `except Exception` dalina dusup saglayiciyi cezalandiriyordu.
# Artik gecici sayiliyor ve gunluge "siniflandirilmamis" diye yaziliyor.
_UNCLASSIFIED_ERRORS = _exc("CouldNotRetrieveTranscript")

# `_select_transcript` icin: tercih edilen dil bulunamadi sinyali ve ceviri
# denemesinin basarisiz olma bicimleri. Ikisi de AKIS KONTROLU, hata degil.
_NO_TRANSCRIPT_FOUND = _exc("NoTranscriptFound")
_TRANSLATION_ERRORS = _exc("NotTranslatable", "TranslationLanguageNotAvailable")


@dataclass
class YouTubeTranscriptAPIProvider:
    """Zincirin ILK halkasi -- ve tek basina en cok engellenen halka.

    `config` ZORUNLU: bu saglayici uzun sure `YouTubeTranscriptApi()` diye
    parametresiz kuruluyordu, yani `YTDLP_PROXY` ve cerezler yalnizca yt-dlp
    yoluna uygulaniyor, ilk istek her zaman sunucunun ciplak IP'sinden
    cikiyordu. Veri merkezi IP'leri YouTube'un ilk engelledigi seydir.
    """

    config: AppConfig
    name: str = "youtube_transcript_api"
    logger: object | None = None

    def _api(self) -> YouTubeTranscriptApi:
        """Yapilandirilmis kimligi tasiyan TAZE bir istemci.

        Her cagrida yeni: `youtube-transcript-api` belgesinde "thread basina bir
        ornek olusturun" diyor (icerideki `requests.Session` thread-safe degil)
        ve transkriptler `MAX_TRANSCRIPT_WORKERS` thread'inde paralel cekiliyor.
        """
        return YouTubeTranscriptApi(http_client=build_session(self.config, self.logger))

    def _preferred_languages(self, language_hint: str | None) -> list[str]:
        languages: list[str] = []
        for language in [language_hint, "en", "tr"]:
            if language and language not in languages:
                languages.append(language)
        return languages

    def fetch(self, video_id: str, language_hint: str | None = None) -> TranscriptResult | None:
        """Tek cagrida tercih sirasina gore altyazi ceker.

        Onceki surum kaldirilmis olan `YouTubeTranscriptApi.get_transcript` statik
        metodunu cagiriyordu (1.x'te AttributeError). Ayrica dilleri tek tek
        deniyordu ve ilk dildeki istisna donguyu kiriyordu.
        """
        languages = self._preferred_languages(language_hint)
        try:
            segments, language_code = self._fetch_raw(video_id, languages)
        except _VIDEO_LEVEL_ERRORS as exc:
            # Bu videoda altyazi yok/kapali: saglayici saglikli, sadece video uygun degil.
            raise VideoUnavailableError(str(exc)) from exc
        except _RATE_LIMIT_ERRORS as exc:
            # IP blogu: tekrar denemek YouTube'un gozunde durumu kotulestirir.
            raise ProviderRateLimitedError(str(exc)) from exc
        except _CONFIG_ERRORS as exc:
            # YouTube "bunu bir tarayicidan istedigini kanitla" diyor. Ne tekrar
            # denemek ne de dinlendirmek bunu duzeltir; eksik olan yapilandirma.
            raise ProviderPermanentError(
                f"{exc}\n"
                "YouTube bu istek için PO token istiyor. `.env` içinde "
                "YTDLP_COOKIES_FILE ile oturum açmış bir tarayıcının çerezlerini "
                "verin ya da YTDLP_PROXY ile konut (residential) IP kullanın."
            ) from exc
        except _INFRA_ERRORS as exc:
            raise ProviderTemporaryError(str(exc)) from exc
        except _UNCLASSIFIED_ERRORS as exc:
            # EN SONDA olmali: `CouldNotRetrieveTranscript` yukaridaki tum
            # istisnalarin ust sinifi, once gelirse hepsini yutar.
            if self.logger:
                self.logger.warning(
                    "Sınıflandırılmamış transkript hatası (%s): %s", type(exc).__name__, exc
                )
            raise ProviderTemporaryError(str(exc)) from exc

        # `text` segmentlerden TURETILIYOR: iki alanin ayri ayri uretilmesi,
        # birinin degisip digerinin degismedigi bir ayrisma noktasi olurdu.
        text = normalize_text(" ".join(segment.text for segment in segments))
        if len(text) < MIN_TRANSCRIPT_CHARS:
            return None
        return TranscriptResult(
            video_id=video_id,
            status="available",
            source=self.name,
            language=language_code,
            text=text,
            segments=segments,
        )

    def _fetch_raw(
        self, video_id: str, languages: list[str]
    ) -> tuple[list[TranscriptSegment], str | None]:
        """Altyaziyi ceker; zaman damgalari KORUNUYOR.

        `fetch(languages=...)` yerine `list()` uzerinden gidiliyor. Tek satirlik
        `fetch` yalnizca tercih edilen dilleri deneyip yoksa `NoTranscriptFound`
        firlatiyordu; oysa elimizde hangi dillerin VAR oldugu bilgisi de var ve
        onu gormeden vazgecmek, yalnizca Almanca altyazisi olan bir videoyu
        "altyazisiz" saymak demekti (bkz. `_select_transcript`).

        0.6.x (`get_transcript`) dali KALDIRILDI: `requirements.txt` 1.0+
        pinliyor, dolayisiyla o dal hicbir zaman calismiyordu -- ama calissaydi
        statik cagri oldugu icin yapilandirilmis proxy/cerezleri baypas ederdi.
        """
        transcript_list = self._api().list(video_id)
        transcript = _select_transcript(transcript_list, languages)
        fetched = transcript.fetch()
        rows = [
            (
                getattr(snippet, "text", "") or "",
                getattr(snippet, "start", None),
                getattr(snippet, "duration", None),
            )
            for snippet in fetched
        ]
        language_code = getattr(fetched, "language_code", None) or getattr(
            transcript, "language_code", None
        )
        return _to_segments(rows), language_code


def _select_transcript(transcript_list, languages: list[str]):
    """Tercih edilen dil yoksa ELDEKINE duser.

    Eskiden yalnizca `[ipucu, "en", "tr"]` deneniyordu ve hicbiri yoksa
    `NoTranscriptFound` firlatiliyordu. Bunun iki bedeli vardi: (1) yalnizca
    Almanca altyazisi olan bir video "altyazisiz" sayiliyordu, (2) bu durum
    videoya ozgu KALICI kabul edilip 30 GUN onbellege yaziliyordu -- oysa
    yasanan sey bir dil eslesmemesiydi, altyazinin yoklugu degil.

    Ayrica yt-dlp yolu (`_select_caption_track`) zaten "kalan tum diller"e
    dusuyordu; iki saglayici ayni videoda farkli karar veriyordu.

    Sira: tercih edilen diller -> elle girilmis altyazi -> otomatik altyazi.
    Secilen altyazi cevrilebiliyorsa tercih edilen ilk dile cevriliyor; ceviri
    basarisiz olursa HAM metinle devam ediliyor (yanlis dilde bir transkript,
    hic transkript olmamasindan iyidir).
    """
    try:
        return transcript_list.find_transcript(languages)
    except _NO_TRANSCRIPT_FOUND:
        available = list(transcript_list)
        if not available:
            # Gercekten hicbir altyazi yok: bu videoya ozgu kalici durum.
            raise

    # `is_generated` False < True: elle girilmis olan once gelir. `min` ilk
    # asgari ogeyi dondurdugu icin esitlikte kaynak sirasi korunur.
    chosen = min(available, key=lambda item: bool(getattr(item, "is_generated", True)))

    if getattr(chosen, "is_translatable", False):
        targets = {
            getattr(language, "language_code", None)
            for language in getattr(chosen, "translation_languages", []) or []
        }
        for language in languages:
            if language in targets:
                try:
                    return chosen.translate(language)
                except _TRANSLATION_ERRORS:
                    break
    return chosen


def _to_segments(rows) -> list[TranscriptSegment]:
    """`(metin, baslangic, sure)` uclulerini segmentlere cevirir.

    Zamani OKUNAMAYAN satir metniyle birlikte ATILMIYOR: 0.0'a sabitlenip
    tutuluyor. Metnin kendisi transkriptin bir parcasi ve onu dusurmek, arama
    havuzundan gercek icerik eksiltmek olurdu -- yanlis olan yalnizca konumu ve
    o durumda alinti saniye gostermez.
    """
    segments: list[TranscriptSegment] = []
    for text, start, duration in rows:
        text = (text or "").strip()
        if not text:
            continue
        try:
            start_sec = max(0.0, float(start))
        except (TypeError, ValueError):
            start_sec = 0.0
        end_sec = None
        try:
            if duration is not None and float(duration) > 0:
                end_sec = start_sec + float(duration)
        except (TypeError, ValueError):
            end_sec = None
        segments.append(TranscriptSegment(start_sec=start_sec, end_sec=end_sec, text=text))
    return segments
