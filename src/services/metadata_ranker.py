from __future__ import annotations

import math
from datetime import datetime, timezone

from src.models import FilterOptions, MetadataScore, VideoCandidate
from src.utils.text_utils import CorpusWeights, coverage_score, normalize_text


# --------------------------------------------------------------------------- #
# Alaka agirliklari
#
# Kritik nokta: sorgu eskiden "konu + alt konu" seklinde TEK bir token kumesine
# birlestiriliyordu. Ortak konu tokenlari baskin geldigi icin butun alt konular
# neredeyse ayni siralamayi uretiyordu ("Zaman serisi temelleri" ile "ARIMA
# modeli" ayni videoyu kazandiriyordu). Artik iki sinyal AYRI olculuyor ve
# ayirt edici olan alt konuya daha yuksek agirlik veriliyor.
# --------------------------------------------------------------------------- #
# Konu bileseni bilerek DUSUK tutuluyor. Aday havuzu zaten konuya gore
# arandigi icin "konuyla ilgili mi" sorusu buyuk olcude cevaplanmis durumda;
# yuksek konu agirligi pratikte konu ifadesinin harfi harfine tekrarini
# odullendiriyor. Bu iki dilli modda yapisal bir yanliliga yol aciyordu:
# Ingilizce bir video Turkce konu kelimelerini hic karsilayamadigi icin, dil
# puanina EK olarak alaka puanindan da kaybediyordu.
TITLE_SUBTOPIC_WEIGHT = 3.2
TITLE_TOPIC_WEIGHT = 0.8
DESCRIPTION_SUBTOPIC_WEIGHT = 1.6
DESCRIPTION_TOPIC_WEIGHT = 0.4

# Populerlik sinyallerinin (kanal otoritesi, tazelik, etkilesim) tam agirlikla
# sayilmasi icin gereken alaka esigi: `baslik + aciklama` alakasi bu degere
# ulasinca kapi tamamen aciliyor, sifirken tamamen kapali, arada dogrusal.
#
# Deger 13 kayitli kosu (42 secim) uzerinde olcuklerek secildi. Esik taramasi:
#
#   kapali -> ortanca alaka 1.30, sinyalsiz secim 6/42
#   0.75   -> 1.30, 6/42   (hicbir sey degismiyor)
#   1.00   -> 1.30, 6/42   (hicbir sey degismiyor)
#   1.50   -> 1.39, 4/42   <-- en kucuk etkili deger
#   2.00   -> 1.39, 4/42   (1.5 ile ayni)
#   3.00   -> 1.44, 4/42   (kazanci yok, riski var)
#
# 1.5 kazanci saglayan EN KUCUK esik; daha yukarisi ayni sonucu verirken dogru
# secimleri de cezalandirma riskini buyutuyor.
POPULARITY_GATE_FULL = 1.5

# Aciklamanin ilk bolumu konuyu anlatir; devami genelde link/reklam yigini olur.
DESCRIPTION_SAMPLE_CHARS = 1200

# Kullanicinin sure sinirini asan videolar icin uygulanan sabit ceza.
OVER_LIMIT_PENALTY = -3.0

# "Ogretici olacak kadar uzun" esigi. Kullanicinin sure tavani bunun altina
# indiginde taban da iniyor; bkz. `_duration_fit_score`.
IDEAL_MIN_DURATION_SEC = 480

# `confidence_score` icin kalibrasyon araligi. Teorik maksimum (~15) pratikte asla
# gorulmedigi icin gercekci bir bant kullaniliyor; aksi halde tum videolar 4.7-5.8
# arasina sikisiyor ve guven puani ayirt edici olmuyordu.
SCORE_FLOOR = -1.0
SCORE_CEILING = 10.5


def rank_candidates(
    candidates: list[VideoCandidate],
    topic: str,
    subtopic: str,
    filters: FilterOptions,
    subtopic_terms: list[str] | None = None,
) -> list[tuple[VideoCandidate, MetadataScore]]:
    """Adaylari puanlar ve siralar.

    Sure sinirini asan adaylar, sinir icindeki tum adaylarin ARDINA yerlestirilir.
    Boylece "Max duration" gercek bir kisit gibi davranir, ama uygun aday yoksa
    liste bos kalmaz.
    """
    within_limit: list[tuple[VideoCandidate, MetadataScore]] = []
    over_limit: list[tuple[VideoCandidate, MetadataScore]] = []

    # Aday havuzunun kendisi korpus: nadir terimler ("xgboost") yuksek, yaygin
    # terimler ("model", "zaman") dusuk agirlik alir. Havuz basina BIR kez.
    weights = CorpusWeights(candidate.title for candidate in candidates)

    for original in candidates:
        score = score_candidate(
            original,
            topic,
            filters,
            subtopic=subtopic,
            weights=weights,
            subtopic_terms=subtopic_terms,
        )
        # KOPYA uzerinde calis: ayni aday havuzu birden fazla alt konu icin
        # siralandiginda `metadata_score` alani birbirinin uzerine yaziliyordu ve
        # tanilama ciktisi yanlis puan gosteriyordu.
        candidate = original.model_copy(update={"metadata_score": score.total})
        if _exceeds_duration_limit(candidate.duration_sec, filters.max_duration_minutes):
            over_limit.append((candidate, score))
        else:
            within_limit.append((candidate, score))

    within_limit.sort(key=lambda item: item[1].total, reverse=True)
    over_limit.sort(key=lambda item: item[1].total, reverse=True)
    return within_limit + over_limit


def score_candidate(
    candidate: VideoCandidate,
    topic: str,
    filters: FilterOptions,
    subtopic: str | None = None,
    weights: CorpusWeights | None = None,
    subtopic_terms: list[str] | None = None,
) -> MetadataScore:
    topic_text = normalize_text(topic)
    subtopic_text = normalize_text(subtopic or "")
    description = candidate.description[:DESCRIPTION_SAMPLE_CHARS]

    terms = [normalize_text(term) for term in (subtopic_terms or []) if normalize_text(term)]

    title_relevance = _weighted_relevance(
        candidate.title, topic_text, subtopic_text, TITLE_SUBTOPIC_WEIGHT, TITLE_TOPIC_WEIGHT, weights, terms
    )
    description_relevance = _weighted_relevance(
        description, topic_text, subtopic_text, DESCRIPTION_SUBTOPIC_WEIGHT, DESCRIPTION_TOPIC_WEIGHT, weights, terms
    )
    channel_quality = _channel_quality_score(candidate)
    duration_fit = _duration_fit_score(candidate.duration_sec, filters.max_duration_minutes)
    difficulty_fit = _difficulty_fit_score(candidate, filters.difficulty)
    language_match = _language_match_score(candidate.language, filters.language, filters.include_english)
    freshness = _freshness_score(candidate.publish_date, filters.freshness_preference)
    engagement = _engagement_score(candidate)
    penalty = -2.0 if candidate.is_live else 0.0

    # Populerlik sinyalleri ALAKAYA BAGLI olarak aciliyor.
    #
    # Olculen sorun: alaka disi sinyallerin tavani (kanal 2.2 + sure 2.0 + dil 1.5
    # + tazelik 1.5 + etkilesim 1.0 = 8.2) alaka sinyallerininkinden (baslik 3.2 +
    # aciklama 1.6 = 4.8) YUKSEKTI. Sonuc: konuyla hic eslesmeyen ama populer bir
    # video slotu kazanabiliyordu. Kayitli 42 oneride 10'u boyle secilmisti --
    # ornegin "Ivmeolcer ve Jiroskop Veri Birlestirme" alt basligina "Kalman
    # Filtresi ve BORSA Uygulamasi" atanmisti (baslik alakasi 0.12, toplam 5.32,
    # yani 5.2'si alaka disi sinyallerden).
    #
    # Kapi yalnizca POPULERLIK sinyallerine uygulaniyor (kanal otoritesi, tazelik,
    # etkilesim). Sure/dil/zorluk KULLANICI KISITI; onlari alakaya baglamak
    # "60 dakikayi asmasin" gibi bir tercihi sessizce zayiflatirdi.
    relevance = title_relevance + description_relevance
    gate = min(1.0, relevance / POPULARITY_GATE_FULL) if POPULARITY_GATE_FULL else 1.0
    popularity = (channel_quality + freshness + engagement) * gate

    total = (
        title_relevance
        + description_relevance
        + popularity
        + duration_fit
        + difficulty_fit
        + language_match
        + penalty
    )

    rationale = _build_rationale(
        candidate=candidate,
        filters=filters,
        title_relevance=title_relevance,
        description_relevance=description_relevance,
        channel_quality=channel_quality,
        duration_fit=duration_fit,
        difficulty_fit=difficulty_fit,
        language_match=language_match,
        freshness=freshness,
        engagement=engagement,
    )

    return MetadataScore(
        total=round(total, 3),
        title_relevance=round(title_relevance, 3),
        description_relevance=round(description_relevance, 3),
        channel_quality=round(channel_quality, 3),
        duration_fit=round(duration_fit, 3),
        difficulty_fit=round(difficulty_fit, 3),
        language_match=round(language_match, 3),
        freshness=round(freshness, 3),
        engagement=round(engagement, 3),
        rationale=rationale,
    )


def to_confidence(total: float) -> float:
    """Ham metadata toplamini 0..10 araligina esler."""
    span = SCORE_CEILING - SCORE_FLOOR
    normalized = (total - SCORE_FLOOR) / span * 10.0
    return round(max(0.0, min(10.0, normalized)), 2)


def _weighted_relevance(
    text: str,
    topic: str,
    subtopic: str,
    subtopic_weight: float,
    topic_weight: float,
    weights: CorpusWeights | None = None,
    terms: list[str] | None = None,
) -> float:
    topic_coverage = coverage_score(text, topic, weights=weights) if topic else 0.0
    if not subtopic:
        # Alt konu yoksa tum agirlik konuya gider (skalayi korumak icin).
        return (subtopic_weight + topic_weight) * topic_coverage
    # Havuz istatistigi varsa IDF, yoksa konu tokenlarini "arka plan" sayan
    # kaba yontem kullanilir; her iki durumda da ayirt edici terimler agirligi tasir.
    subtopic_coverage = coverage_score(text, subtopic, background=topic, weights=weights)

    # Esdeger terimler: ayni kavramin BASKA ADLARI (Ingilizce karsiligi,
    # kisaltmasi). Leksik eslestirme "Kokusuz" ile "Unscented"i birbirine
    # baglayamiyordu ve o alt konu ingilizce videolarla hic eslesmiyordu.
    #
    # TOPLAMA DEGIL EN IYISINI ALMA: terimler ayni kavrami anlattigi icin
    # toplamak, cok adi olan kavramlari yapay olarak one cikarirdi. Videonun
    # kavrami hangi adla andigi onemli degil; onemli olan andigi.
    for term in terms or []:
        subtopic_coverage = max(
            subtopic_coverage, coverage_score(text, term, background=topic, weights=weights)
        )

    return subtopic_weight * subtopic_coverage + topic_weight * topic_coverage


def _exceeds_duration_limit(duration_sec: int | None, max_duration_minutes: int) -> bool:
    if not duration_sec:
        return False
    return duration_sec > max_duration_minutes * 60


def _channel_quality_score(candidate: VideoCandidate) -> float:
    """Kanal otoritesi: once gercek abone verisi, yoksa isim ipuclari.

    Onceki surum yalnizca kanal ADINDA gecen kelimelere bakiyordu; adinda
    "tutorial" gecen rastgele bir kanal, MIT OpenCourseWare'den yuksek puan
    aliyordu. Ayrica izlenme sayisini burada bir kez daha sayarak `engagement`
    ile cift sayim yapiyordu.
    """
    score = 0.0
    subscribers = candidate.subscriber_count

    if subscribers and subscribers > 0:
        # Logaritmik: 1k -> 0.0, 10k -> 0.5, 100k -> 1.0, 1M -> 1.5, 10M+ -> 2.0
        score += _clamp((math.log10(subscribers) - 3.0) / 2.0, 0.0, 2.0)
    else:
        # Abone verisi yok (yt-dlp yolu): zayif isim sinyaline geri don.
        channel = (candidate.channel or "").lower()
        trusted_terms = ["official", "academy", "university", "course", "institute", "docs",
                         "akademi", "üniversite", "universite"]
        if any(term in channel for term in trusted_terms):
            score += 0.5
        # Isim sinyali guvenilmez oldugu icin notr-alti bir taban veriyoruz.
        score += 0.4

    # Konusuna odaklanmis, uretken kanallar icin kucuk bir ek.
    if candidate.channel_video_count and candidate.channel_video_count >= 50:
        score += 0.2

    return _clamp(score, 0.0, 2.2)


def _duration_fit_score(duration_sec: int | None, max_duration_minutes: int) -> float:
    if not duration_sec:
        return 0.25
    upper_bound = max_duration_minutes * 60
    if duration_sec > upper_bound:
        return OVER_LIMIT_PENALTY

    # "Ideal sure" tabani normalde 8 dakika, AMA kullanicinin tavani bunun
    # altindaysa taban da inmeli. Eskiden sabit 480 yaziliyordu ve tavan 8
    # dakikanin altina cekildiginde `480 <= sure <= tavan` araligi BOSALIYORDU:
    # hicbir video "ideal" puanini alamiyor, en iyi ihtimalle 1.25 aliyordu.
    # Yani "kisa videolar istiyorum" diyen kullanici icin bu sinyal tumden
    # sessizlesiyordu.
    #
    # Tavan 8 dakika ve UZERINDEYSE davranis birebir eskisi gibi kaliyor;
    # degisen yalnizca bozuk olan durum.
    ideal_floor = IDEAL_MIN_DURATION_SEC
    if upper_bound < IDEAL_MIN_DURATION_SEC:
        ideal_floor = upper_bound // 2

    if ideal_floor <= duration_sec <= upper_bound:
        return 2.0
    if 180 <= duration_sec:
        return 1.25
    return 0.5


def _language_match_score(
    candidate_language: str | None, requested_language: str, include_english: bool = False
) -> float:
    if not candidate_language:
        return 0.25
    candidate = candidate_language.lower()
    if candidate.startswith(requested_language.lower()):
        return 1.5
    # Iki dilli kesif acikken Ingilizce icerik BILEREK havuza aliniyor; ceza
    # vermek kendi amacimizla celisir. Yine de ana dil daha yuksek puan alir,
    # boylece Turkce karsiligi varken Ingilizce one gecmez.
    if include_english and candidate.startswith("en"):
        return 0.6
    return -0.2


def _difficulty_fit_score(candidate: VideoCandidate, difficulty: str) -> float:
    if difficulty == "mixed":
        return 0.25
    text = normalize_text(f"{candidate.title} {candidate.description[:DESCRIPTION_SAMPLE_CHARS]}").lower()
    if difficulty == "beginner":
        beginner_terms = ["beginner", "intro", "introduction", "basics", "fundamentals", "start",
                          "başlangıç", "baslangic", "temel", "giriş", "giris", "sıfırdan", "sifirdan"]
        return 1.0 if any(term in text for term in beginner_terms) else 0.1
    if difficulty == "advanced":
        advanced_terms = ["advanced", "deep dive", "architecture", "internals", "expert",
                          "ileri", "derinlemesine", "uzman", "gelişmiş", "gelismis"]
        return 1.0 if any(term in text for term in advanced_terms) else -0.1
    intermediate_terms = ["intermediate", "project", "applied", "practical", "walkthrough",
                          "orta", "proje", "uygulamalı", "uygulamali", "pratik"]
    return 0.75 if any(term in text for term in intermediate_terms) else 0.1


def _freshness_score(publish_date: str | None, preference: str) -> float:
    age_days = _age_in_days(publish_date)
    if age_days is None:
        return 0.0
    if preference == "recent":
        if age_days <= 365:
            return 1.5
        if age_days <= 730:
            return 0.75
        return -0.25
    if preference == "evergreen":
        if 180 <= age_days <= 1825:
            return 1.0
        return 0.5
    if age_days <= 1095:
        return 1.0
    return 0.25


def _engagement_score(candidate: VideoCandidate) -> float:
    """Yasa gore normalize edilmis ilgi: gunluk izlenme.

    Ham izlenme sayisi eski videolari kayiriyordu: 5 yillik 100k izlenme ile
    2 haftalik 100k izlenme ayni puani aliyordu. Ayrica bu sinyal artik yalnizca
    burada sayiliyor (kanal kalitesinde tekrar edilmiyor).
    """
    if not candidate.view_count or candidate.view_count <= 0:
        return 0.0
    age_days = _age_in_days(candidate.publish_date)
    if age_days is None:
        # Tarih bilinmiyorsa ham izlenmeye gore muhafazakar bir puan ver.
        return _clamp((math.log10(candidate.view_count) - 3.0) / 3.0, 0.0, 0.6)
    views_per_day = candidate.view_count / max(age_days, 7)
    # 1/gun -> 0.0, 10/gun -> 0.33, 100/gun -> 0.67, 1000+/gun -> 1.0
    return _clamp(math.log10(max(views_per_day, 1.0)) / 3.0, 0.0, 1.0)


def _build_rationale(**parts) -> list[str]:
    candidate: VideoCandidate = parts["candidate"]
    filters: FilterOptions = parts["filters"]
    rationale: list[str] = []

    if parts["title_relevance"] >= 2.0:
        rationale.append("başlık bu alt konuyla doğrudan eşleşiyor")
    elif parts["title_relevance"] >= 1.0:
        rationale.append("başlık konuyla ilişkili")
    if parts["description_relevance"] >= 1.0:
        rationale.append("açıklama konu uyumunu destekliyor")
    if parts["channel_quality"] >= 1.5:
        subscribers = candidate.subscriber_count
        if subscribers:
            rationale.append(f"köklü kanal ({_compact_number(subscribers)} abone)")
        else:
            rationale.append("kanal sinyalleri güvenilir görünüyor")
    if parts["duration_fit"] >= 1.5:
        rationale.append("süre çalışma seansına uygun")
    if parts["difficulty_fit"] >= 0.75:
        rationale.append("istenen seviyeye uygun")
    if parts["language_match"] >= 1.0:
        rationale.append("dil tercihiyle eşleşiyor")
    if parts["freshness"] >= 1.0:
        rationale.append("yayın tarihi tazelik tercihine uygun")
    if parts["engagement"] >= 0.67:
        rationale.append("yaşına göre güçlü izlenme")
    if _exceeds_duration_limit(candidate.duration_sec, filters.max_duration_minutes):
        rationale.append("istenen azami süreyi aşıyor")
    if candidate.is_live:
        rationale.append("canlı yayın olduğu için geri plana atıldı")
    return rationale


def _age_in_days(publish_date: str | None) -> int | None:
    if not publish_date:
        return None
    published = _parse_date(publish_date)
    if not published:
        return None
    return max((datetime.now(timezone.utc) - published).days, 0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return str(value)


def _parse_date(value: str) -> datetime | None:
    formats = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y%m%d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
