"""Metni erisim (retrieval) icin parcalara boler.

SAF: ag yok, LLM yok, veritabani yok. Bu bilerek -- parcalama kurallari
RAG'in en cok ayar isteyen yeri ve her ayari birim testiyle dogrulanabilir
olmali.

Neden parcaliyoruz: bir ogrenme alani onlarca video ve dokuman tasiyabiliyor,
toplami hicbir baglam penceresine sigmaz. Tek bir transkript sigiyordu ve
`playlist_service._generate_study_notes` tam da bu yuzden parcalama
KULLANMIYOR -- o karar bugun de dogru, degisen sey kaynak sayisi.

Iki cikti kuralı bu modulun tamamini yonetiyor:

1. **Zaman/sayfa UYDURULMAZ.** Kaynak konum bilgisi vermiyorsa `start_sec` ve
   `page` `None` kalir. Orantiyla tahmin edilen bir saniye ("metnin %40'inda,
   video 10 dakika, demek ki 4. dakika") cogu zaman yanlis olur ve alintiyi
   tiklayan kullaniciyi alakasiz bir yere goturur. Yanlis konum, konumsuz
   alintidan kotudur.
2. **Sinirda kalan cevap ikiye bolunmemeli.** Ardisik parcalar `overlap_chars`
   kadar ortusuyor; aksi halde tam sinira denk gelen bir aciklama iki parcada
   da yarim kalir ve ikisi de sorunun cevabini tasimaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models import TranscriptSegment
from src.utils.text_utils import normalize_text


# Cumle sonu: nokta/soru/unlem + bosluk. Kisaltmalar ("vb.", "Dr.") yanlis
# bolunebilir -- kabul ediyoruz, cunku bedeli yalnizca parca sinirinin birkac
# kelime kaymasi ve ORTUSME zaten bunu telafi ediyor. Tam bir cumle bolucu
# (dile ozgu kisaltma sozlugu) buradaki kazanca degmez.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


@dataclass(frozen=True)
class ChunkDraft:
    """Henuz veritabanina yazilmamis bir parca."""

    ordinal: int
    text: str
    start_sec: float | None = None
    end_sec: float | None = None
    page: int | None = None


@dataclass(frozen=True)
class _Piece:
    """Parcalanamaz en kucuk birim: bir cumle ya da bir transkript segmenti."""

    text: str
    start_sec: float | None = None
    end_sec: float | None = None
    page: int | None = None


def chunk_transcript(
    segments: list[TranscriptSegment],
    text: str | None,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """Bir video transkriptini parcalara boler.

    `segments` VARSA parca sinirlari segment sinirlarina oturur ve `start_sec`
    GERCEK olur -- tahmin degil. Segment yoksa (eski onbellek kaydi ya da zaman
    bilgisi vermeyen bir saglayici) duz metin uzerinden bolunur ve zaman
    damgasi `None` kalir.
    """
    if segments:
        pieces = [
            _Piece(
                text=normalize_text(segment.text),
                start_sec=segment.start_sec,
                end_sec=segment.end_sec,
            )
            for segment in segments
            if normalize_text(segment.text)
        ]
    else:
        pieces = [_Piece(text=sentence) for sentence in _split_sentences(text or "")]
    return _pack(pieces, max_chars=max_chars, overlap_chars=overlap_chars)


def chunk_document(
    pages: list[tuple[int, str]],
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """Bir dokumani parcalara boler. `pages` = `(sayfa_no, metin)` listesi.

    Parcalar sayfa sinirini GECEBILIR ve alintida parcanin BASLADIGI sayfa
    gosterilir. Alternatifi her sayfayi ayri parcalamakti; slayt ya da kisa
    sayfali dokumanlarda bu, baglami kaybeden onlarca minik parca uretiyordu.
    Sayfa numarasi zaten kesin bir konum degil bir isaret -- "4. sayfadan
    itibaren" dogru ve yeterli.
    """
    pieces: list[_Piece] = []
    for page_number, page_text in pages:
        for sentence in _split_sentences(page_text):
            pieces.append(_Piece(text=sentence, page=page_number))
    return _pack(pieces, max_chars=max_chars, overlap_chars=overlap_chars)


def _split_sentences(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    return [part.strip() for part in _SENTENCE_END.split(normalized) if part.strip()]


def _hard_split(piece: _Piece, max_chars: int) -> list[_Piece]:
    """`max_chars`tan uzun tek bir parcayi zorla boler.

    Ortusme mantiginin ILERLEME garantisi buna dayaniyor: tek basina sigmayan
    bir parca kalirsa paketleyici onu hicbir zaman yerlestiremez ve sonsuz
    donguye girer. Pratikte noktalama kullanmayan otomatik altyazilarda
    goruluyor (tum transkript tek "cumle").

    Bolunen parcalarin HEPSI ayni zaman/sayfa bilgisini tasir: ikinci yarinin
    gercek baslangicini bilmiyoruz ve tahmin etmek bu modulun 1. kuralini
    cignerdi.
    """
    if len(piece.text) <= max_chars:
        return [piece]

    def same_place(text: str) -> _Piece:
        return _Piece(text, piece.start_sec, piece.end_sec, piece.page)

    parts: list[_Piece] = []
    current = ""
    for word in piece.text.split(" "):
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            parts.append(same_place(current))
            current = word
        else:
            current = candidate
        # Tek bir "kelime" bile sigmiyorsa (uzun URL, bosluksuz metin) karakterden bol.
        while len(current) > max_chars:
            parts.append(same_place(current[:max_chars]))
            current = current[max_chars:]
    if current:
        parts.append(same_place(current))
    return parts


def _pack(pieces: list[_Piece], *, max_chars: int, overlap_chars: int) -> list[ChunkDraft]:
    """Parcalari `max_chars` siniri ve `overlap_chars` ortusmesiyle paketler."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0")
    # Ortusme parcanin kendisinden buyuk olamaz; olsaydi her yeni parca bir
    # oncekinin tamamiyla baslar ve ilerleme durur.
    overlap_chars = max(0, min(overlap_chars, max_chars // 2))

    expanded: list[_Piece] = []
    for piece in pieces:
        expanded.extend(_hard_split(piece, max_chars))

    chunks: list[ChunkDraft] = []
    current: list[_Piece] = []
    length = 0
    # `current`in basindaki kac parca ONCEKI chunk'tan ortusme olarak devredildi.
    # Son chunk'in "saf tekrar" olup olmadigini bu sayi yanitliyor.
    carried = 0

    def emit() -> None:
        chunks.append(
            ChunkDraft(
                ordinal=len(chunks),
                text=" ".join(item.text for item in current),
                # Konum ILK parcadan: chunk metnin "burasindan itibaren"
                # geldigini soyluyor.
                start_sec=current[0].start_sec,
                # Bitis SON parcadan; o da bilmiyorsa `None`.
                end_sec=current[-1].end_sec,
                page=current[0].page,
            )
        )

    def flush() -> None:
        nonlocal current, length, carried
        if not current:
            return
        emit()
        current = _tail_for_overlap(current, overlap_chars)
        carried = len(current)
        length = sum(len(item.text) + 1 for item in current)

    for piece in expanded:
        addition = len(piece.text) + (1 if current else 0)
        if current and length + addition > max_chars:
            flush()
            addition = len(piece.text) + (1 if current else 0)
            # Ortusme kuyrugu tek basina buyuk olabilir: `_tail_for_overlap`
            # ilk parcayi boyuna BAKMADAN aliyor (aksi halde hic kuyruk
            # kalmazdi). Kuyruk + yeni parca tavani asiyorsa kuyrugu BIRAKIYORUZ.
            #
            # Eskiden bu kontrol yoktu ve flush sonrasi tavan bir daha hic
            # bakilmadan asiliyordu: olculdu, `max_chars=200` iken 379
            # karakterlik parca uretiliyordu (1.9x). Varsayilan 1.200'de bu
            # ~2.280 karaktere denk geliyor -- gomme cagrisinin sinirina
            # yaklasan ve erisim kesinligini seyrelten bir sapma.
            #
            # Bedeli o sinirdaki ortusmeyi kaybetmek; alternatifi ise modulun
            # tek sert guvencesini (parca tavani) cignemek. Her parca
            # `_hard_split` sonrasi tavanin altinda oldugu icin bu dal
            # tavani KESIN kiliyor.
            if current and length + addition > max_chars:
                current = []
                carried = 0
                length = 0
                addition = len(piece.text)
        current.append(piece)
        length += addition

    # Son `flush` bir ortusme kuyrugu birakmis olabilir. Kuyruga YENI parca
    # eklenmediyse elimizdeki sey onceki chunk'in aynen tekrari demektir ve
    # yazilmamali.
    #
    # Olcut METIN KARSILASTIRMASI DEGIL, parca sayisi: metne bakan bir kontrol
    # ("son chunk oncekinin icinde geciyor mu") gercekten tekrar eden icerigi
    # de siliyordu -- ornegin noktalamasiz bir altyazinin zorla bolunmesinden
    # cikan ozdes parcalari. Ayni gorunen iki parca ayni parca degildir;
    # farkli konumlardan gelirler.
    if current and len(current) > carried:
        emit()
    return chunks


def _tail_for_overlap(pieces: list[_Piece], overlap_chars: int) -> list[_Piece]:
    """Bir sonraki parcanin basina konacak kuyruk."""
    if overlap_chars <= 0:
        return []
    tail: list[_Piece] = []
    total = 0
    for piece in reversed(pieces):
        if total + len(piece.text) > overlap_chars and tail:
            break
        tail.insert(0, piece)
        total += len(piece.text) + 1
    # Tamamini geri vermek ilerlemeyi durdurur.
    return tail if len(tail) < len(pieces) else tail[1:]
