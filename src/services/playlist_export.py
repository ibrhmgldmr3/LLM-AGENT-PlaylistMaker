"""Calistirma ciktilarinin uretimi. SAF: disk, ag, veritabani yok.

Ciktilar SONUCUN TURETILMISI: JSON `result.model_dump()`, Markdown ise
asagidaki bicimleyici. Yani `PlaylistResult` elde oldugu surece dosyalar HER
AN yeniden uretilebilir -- diskte saklanmalari bir zorunluluk degil, bir
onbellek.

Bu ayrimin iki somut karsiligi var:

1. `GET /api/runs/{id}/export/{artifact}` artik diskten OKUMUYOR, buradan
   yeniden uretiyor. Indirmeyi karsilayan surecin, dosyayi ureten surec
   olmasi gerekmiyor -- coklu replika onunde duran engellerden biri buydu.
2. Calistirma dizini silinmis olsa bile (elle temizlik, kaybolan birim,
   dolan disk) indirme calismaya devam ediyor. Eskiden bu durumda
   "Dosya sunucudan silinmis" (410) donuyordu, oysa veri SQLite'ta duruyordu.

Diske yazma KALDIRILMADI (`playlist_service.export_playlist_artifacts`):
calistirma dizinindeki dosyalar hala kullanisli ve `ExportArtifacts` sonuc
sozlesmesinin parcasi. Degisen tek sey, INDIRMENIN artik onlara bagli olmamasi.
"""

from __future__ import annotations

import json

from src.models import PlaylistResult

# Indirilebilir cikti turleri: ad -> (dosya adi, MIME turu).
#
# TEK KAYNAK. Bu esleme rota katmaninda ayrica yaziliydi; iki yerde durmasi,
# birine yeni bir tur eklenip digerine eklenmemesi demekti.
EXPORT_KINDS: dict[str, tuple[str, str]] = {
    "json": ("result.json", "application/json"),
    "markdown": ("study_plan.md", "text/markdown"),
}


class UnknownExportKind(KeyError):
    """Istenen cikti turu tanimli degil. Cagiranin hatasi."""


def render_export(result: PlaylistResult, kind: str) -> tuple[str, str, str]:
    """`(icerik, dosya adi, MIME turu)` uretir."""
    try:
        filename, media_type = EXPORT_KINDS[kind]
    except KeyError as exc:
        raise UnknownExportKind(kind) from exc

    if kind == "json":
        content = json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    else:
        content = render_markdown(result)
    return content, filename, media_type


def render_markdown(result: PlaylistResult) -> str:
    lines = [
        f"# Study Plan: {result.topic}",
        "",
        f"Generated at: {result.created_at}",
        "",
        "## Playlist",
        "",
    ]
    if not result.recommendations:
        lines.extend(["_No recommendations were produced._", ""])
    for recommendation in result.recommendations:
        lines.extend(
            [
                f"{recommendation.position}. [{recommendation.video.title}]({recommendation.video.url})",
                f"   - Subtopic: {recommendation.subtopic}",
                f"   - Why: {recommendation.why_selected}",
                f"   - Confidence: {recommendation.confidence_score}",
                f"   - Transcript: {recommendation.transcript_status}",
                "",
            ]
        )
    if result.study_notes:
        lines.extend(["## Study Notes", ""])
        for note in result.study_notes:
            lines.append(f"### {note.subtopic}")
            lines.append("")
            if note.status == "available":
                if note.transcript_source == "asr":
                    backend_label = f" ({note.transcript_backend})" if note.transcript_backend else ""
                    lines.append(f"> _Source: ASR transcript{backend_label}_\n")
                lines.append(note.content or "")
            elif note.status == "no_transcript":
                lines.append("_No transcript was available for this video; no note was generated._")
            else:
                lines.append(f"_Study note generation failed: {note.error}_")
            lines.append("")
    if result.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend([f"- {warning}" for warning in result.warnings])
        lines.append("")
    return "\n".join(lines)
