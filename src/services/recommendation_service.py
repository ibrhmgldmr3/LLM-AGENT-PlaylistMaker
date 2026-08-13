from __future__ import annotations

from src.models import MetadataScore, Recommendation, TranscriptResult, VideoCandidate
from src.services.metadata_ranker import to_confidence
from src.utils.text_utils import keyword_overlap_score, normalize_text


TRANSCRIPT_TEXT_SAMPLE_CHARS = 4000


def assign_recommendations(
    pools: list[tuple[str, list[tuple[VideoCandidate, MetadataScore]]]],
    transcripts: dict[str, TranscriptResult],
    channel_repeat_penalty: float = 0.0,
) -> list[Recommendation | None]:
    """Alt konulara videolari GLOBAL olarak eslestirir.

    Onceki surum her alt konuyu sirayla ele aliyordu: birinci alt konu, aslinda
    ucuncu alt konuya cok daha iyi uyan bir videoyu kapabiliyordu ve ucuncu alt
    konu ikinci tercihiyle yetiniyordu. Burada her turda TUM (alt konu, video)
    ciftleri arasindan en yuksek puanli olan secilir; boylece her video en iyi
    uydugu alt konuya gider.

    Havuzlar kucuk oldugu icin (alt konu x aday ~ 6 x 12) basit yinelemeli
    acgozlu yaklasim yeterli ve deterministiktir.
    """
    assignments: list[Recommendation | None] = [None] * len(pools)
    if not pools:
        return assignments

    # (alt konu, video) -> puan matrisi. Transkript bonusu cifte bagli oldugu icin
    # burada bir kez hesaplanir.
    catalogue: dict[str, tuple[VideoCandidate, MetadataScore]] = {}
    reasons: dict[tuple[int, str], str | None] = {}
    matrix: list[dict[str, float]] = []
    for index, (subtopic, ranked) in enumerate(pools):
        row: dict[str, float] = {}
        for candidate, score in ranked:
            catalogue.setdefault(candidate.video_id, (candidate, score))
            bonus, reason = _transcript_bonus(transcripts.get(candidate.video_id), subtopic)
            row[candidate.video_id] = score.total + bonus
            reasons[(index, candidate.video_id)] = reason
        matrix.append(row)

    choice = _optimise_assignment(matrix, catalogue, channel_repeat_penalty)

    position = 0
    for index, video_id in enumerate(choice):
        if video_id is None:
            continue
        # ADAYI DA puani da ATANAN alt konunun satirindan al. `catalogue` yalnizca
        # kanal bilgisi icin ortak bir dizin; icindeki nesne, videoyu ILK siralayan
        # alt konunun kopyasidir ve `metadata_score` alani o alt konuya aittir.
        # Buradan almak, dis aktarilan JSON'da video.metadata_score ile
        # metadata_score.total'in celismesine yol aciyordu.
        candidate, metadata_score = next(
            pair for pair in pools[index][1] if pair[0].video_id == video_id
        )
        position += 1
        assignments[index] = _build_recommendation(
            pools[index][0],
            candidate,
            metadata_score,
            matrix[index][video_id],
            reasons[(index, video_id)],
            transcripts,
            position=position,
        )
    return assignments


def _optimise_assignment(
    matrix: list[dict[str, float]],
    catalogue: dict[str, tuple[VideoCandidate, MetadataScore]],
    channel_repeat_penalty: float,
) -> list[str | None]:
    """Alt konulara video atar; TOPLAM puani maksimize etmeye calisir.

    Sadece acgozlu davranmak yetmiyor: en yuksek tekil cifti onceden secmek,
    o videoya cok daha fazla ihtiyaci olan baska bir alt konuyu mahrum
    birakabiliyor. Onun icin acgozlu bir baslangicin ustune, toplami arttiran
    takas/degistirme hamleleri uygulanir. Havuzlar kucuk oldugu icin bu tam
    optimuma cok yakin ve deterministik bir sonuc verir.
    """

    def channel_of(video_id: str | None) -> str | None:
        if video_id is None:
            return None
        candidate = catalogue[video_id][0]
        return candidate.channel_id or candidate.channel

    def objective(assignment: list[str | None]) -> float:
        total = 0.0
        seen: set[str] = set()
        for index, video_id in enumerate(assignment):
            if video_id is None:
                continue
            total += matrix[index][video_id]
            channel = channel_of(video_id)
            if channel:
                if channel in seen and channel_repeat_penalty:
                    total -= channel_repeat_penalty
                seen.add(channel)
        return total

    # 1) Acgozlu baslangic: en yuksek puanli (alt konu, video) ciftinden basla.
    assignment: list[str | None] = [None] * len(matrix)
    taken: set[str] = set()
    pending = set(range(len(matrix)))
    while pending:
        best_key = None
        best_move = None
        for index in sorted(pending):
            for order, (video_id, value) in enumerate(matrix[index].items()):
                if video_id in taken:
                    continue
                key = (-value, index, order)
                if best_key is None or key < best_key:
                    best_key, best_move = key, (index, video_id)
        if best_move is None:
            break
        index, video_id = best_move
        assignment[index] = video_id
        taken.add(video_id)
        pending.discard(index)

    # 2) Iyilestirme: takas ve bos videoyla degistirme hamleleri.
    for _ in range(len(matrix) * 2 or 1):
        current = objective(assignment)
        improved = False

        for left in range(len(matrix)):
            for right in range(left + 1, len(matrix)):
                trial = list(assignment)
                trial[left], trial[right] = trial[right], trial[left]
                # Takas ancak her iki video da ilgili alt konunun havuzundaysa gecerli.
                if (trial[left] is not None and trial[left] not in matrix[left]) or (
                    trial[right] is not None and trial[right] not in matrix[right]
                ):
                    continue
                if objective(trial) > current + 1e-9:
                    assignment, current, improved = trial, objective(trial), True

        assigned = {video_id for video_id in assignment if video_id}
        for index in range(len(matrix)):
            for video_id in matrix[index]:
                if video_id in assigned:
                    continue
                trial = list(assignment)
                trial[index] = video_id
                if objective(trial) > current + 1e-9:
                    assignment, current, improved = trial, objective(trial), True
                    assigned = {vid for vid in assignment if vid}

        if not improved:
            break

    return assignment


def _build_recommendation(
    subtopic: str,
    candidate: VideoCandidate,
    metadata_score: MetadataScore,
    combined: float,
    transcript_reason: str | None,
    transcripts: dict[str, TranscriptResult],
    position: int,
) -> Recommendation:
    transcript = transcripts.get(candidate.video_id)
    why_parts = metadata_score.rationale[:]
    if transcript_reason:
        why_parts.append(transcript_reason)
    if not why_parts:
        why_parts.append("en güçlü metadata eşleşmesi olarak seçildi")
    return Recommendation(
        position=position,
        subtopic=subtopic,
        video=candidate,
        why_selected="; ".join(why_parts),
        confidence_score=to_confidence(combined),
        transcript_status=transcript.status if transcript else "unavailable",
        transcript_source=transcript.source if transcript else None,
        transcript_backend=transcript.backend if transcript else None,
        metadata_score=metadata_score,
    )


def select_recommendation(
    subtopic: str,
    ranked_candidates: list[tuple[VideoCandidate, MetadataScore]],
    transcripts: dict[str, TranscriptResult],
    used_video_ids: set[str],
    position: int,
    used_channel_ids: set[str] | None = None,
    channel_repeat_penalty: float = 0.0,
) -> Recommendation | None:
    """Metadata puani + transkript bonusuna gore en iyi kullanilmamis adayi secer.

    Onceki surum yalnizca metadata sirasindaki ilk adayi aliyor, transkript
    bonusunu ise secim yapildiktan SONRA hesapliyordu. Yani pipeline'in en pahali
    adimi (ses indirme + Whisper) secimi hic etkilemiyordu.

    `channel_repeat_penalty` playlist'in tek bir kanala saplanmasini engeller;
    kucuk tutuldugu icin yalnizca yakin skorlu adaylarda belirleyici olur.
    """
    scored: list[tuple[float, int, VideoCandidate, MetadataScore, float, str | None]] = []
    for order, (candidate, metadata_score) in enumerate(ranked_candidates):
        if candidate.video_id in used_video_ids:
            continue
        transcript = transcripts.get(candidate.video_id)
        transcript_bonus, transcript_reason = _transcript_bonus(transcript, subtopic)
        combined = metadata_score.total + transcript_bonus
        if used_channel_ids and channel_repeat_penalty:
            channel_key = candidate.channel_id or candidate.channel
            if channel_key and channel_key in used_channel_ids:
                combined -= channel_repeat_penalty
        # `order` esitlik durumunda metadata sirasini korur (kararli siralama).
        scored.append((combined, order, candidate, metadata_score, transcript_bonus, transcript_reason))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1]))
    combined, _order, candidate, metadata_score, _bonus, transcript_reason = scored[0]

    transcript = transcripts.get(candidate.video_id)
    why_parts = metadata_score.rationale[:]
    if transcript_reason:
        why_parts.append(transcript_reason)
    if not why_parts:
        why_parts.append("en güçlü metadata eşleşmesi olarak seçildi")

    used_video_ids.add(candidate.video_id)
    if used_channel_ids is not None:
        channel_key = candidate.channel_id or candidate.channel
        if channel_key:
            used_channel_ids.add(channel_key)
    return Recommendation(
        position=position,
        subtopic=subtopic,
        video=candidate,
        why_selected="; ".join(why_parts),
        confidence_score=to_confidence(combined),
        transcript_status=transcript.status if transcript else "unavailable",
        transcript_source=transcript.source if transcript else None,
        transcript_backend=transcript.backend if transcript else None,
        metadata_score=metadata_score,
    )


def _transcript_bonus(transcript: TranscriptResult | None, subtopic: str) -> tuple[float, str | None]:
    if transcript is None:
        # Transkript hic denenmedi (kisa liste disinda kaldi): notr.
        return 0.0, None
    if transcript.status != "available" or not transcript.text:
        return -0.1, "transkript alınamadı, seçim metadata'ya dayanıyor"
    overlap = keyword_overlap_score(transcript.text[:TRANSCRIPT_TEXT_SAMPLE_CHARS], normalize_text(subtopic))
    if overlap >= 0.25:
        return 1.0, "transkript içeriği alt konuyu doğruluyor"
    if overlap > 0:
        return 0.35, "transkript alt konuyu kısmen destekliyor"
    return 0.0, "transkript mevcut ama ek kanıt sunmuyor"
