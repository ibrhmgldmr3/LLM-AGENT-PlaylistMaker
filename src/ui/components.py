from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from src.models import PlaylistResult, Recommendation, SubtopicResult


# Bu esigin altindaki oneriler kullaniciya "zayif eslesme" olarak isaretlenir.
WEAK_MATCH_THRESHOLD = 7.0


def header(title: str, subtitle: str) -> None:
    """Hero blogunu TEK bir markdown cagrisiyla basar.

    Streamlit her `st.markdown` cagrisini ayri bir DOM konteynerinde render eder;
    bir cagrida acilip baska cagrida kapatilan <div> hicbir seyi sarmalamaz.
    Onceki surumde .hero/.hero__content stilleri bu yuzden hic uygulanmiyordu.
    """
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero__content">
                <p class="eyebrow">Learning Playlist Generator</p>
                <h1>{html.escape(title)}</h1>
                <p class="subtle">{html.escape(subtitle)}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def panel_intro(title: str, copy: str) -> None:
    st.markdown(
        f"""
        <p class="panel__title">{html.escape(title)}</p>
        <p class="panel__copy">{html.escape(copy)}</p>
        """,
        unsafe_allow_html=True,
    )


def recommendation_card(recommendation: Recommendation) -> None:
    # Gercek bir sarmalayici: Streamlit'in kendi cerceveli konteyneri.
    with st.container(border=True):
        st.markdown(f"### {recommendation.position}. {recommendation.video.title}")
        st.caption(
            f"Subtopic: {recommendation.subtopic} | Confidence: {recommendation.confidence_score}/10 | "
            f"Transcript: {recommendation.transcript_status}"
        )
        # Zayif eslesmeleri sessizce iyi gibi gostermek yerine acikca isaretle.
        # Tipik olarak alt konunun terimleri havuzdaki hicbir baslikta gecmiyordur.
        if recommendation.confidence_score < WEAK_MATCH_THRESHOLD:
            st.caption(
                "⚠️ Zayıf eşleşme — bu alt konu için havuzda iyi bir aday bulunamadı. "
                "Konuyu daraltmayı veya İngilizce içeriği açmayı deneyin."
            )
        st.write(recommendation.why_selected)

        details = []
        if recommendation.video.channel:
            details.append(f"**Channel:** {recommendation.video.channel}")
        if recommendation.video.duration_sec:
            details.append(f"**Duration:** {_format_duration(recommendation.video.duration_sec)}")
        if recommendation.video.view_count:
            details.append(f"**Views:** {recommendation.video.view_count:,}")
        if details:
            st.markdown(" &nbsp;·&nbsp; ".join(details))

        st.link_button("Open Video", recommendation.video.url)
        with st.expander("Metadata breakdown"):
            st.json(recommendation.metadata_score.model_dump())


def subtopic_section(result: SubtopicResult) -> None:
    with st.container(border=True):
        st.markdown(f"#### {result.subtopic.title}")
        st.caption(
            f"Candidates considered: {result.candidates_considered} | "
            f"Transcript status: {result.transcript_status}"
        )
        if result.notes:
            for note in result.notes:
                st.write(f"- {note}")
        if result.shortlisted_candidates:
            st.dataframe(
                [
                    {
                        "title": candidate.title,
                        "channel": candidate.channel,
                        "score": candidate.metadata_score,
                        "provider": candidate.discovery_provider,
                    }
                    for candidate in result.shortlisted_candidates
                ],
                use_container_width=True,
                hide_index=True,
            )


def export_buttons(result: PlaylistResult) -> None:
    if not result.exports:
        st.caption("Bu çalıştırma için dışa aktarım üretilmedi.")
        return
    json_path = Path(result.exports.json_path)
    markdown_path = Path(result.exports.markdown_path)
    columns = st.columns(2)
    if json_path.exists():
        columns[0].download_button(
            "Download JSON",
            data=json_path.read_bytes(),
            file_name=json_path.name,
            mime="application/json",
            use_container_width=True,
        )
    if markdown_path.exists():
        columns[1].download_button(
            "Download Study Plan",
            data=markdown_path.read_bytes(),
            file_name=markdown_path.name,
            mime="text/markdown",
            use_container_width=True,
        )


def _format_duration(duration_sec: int) -> str:
    hours, remainder = divmod(int(duration_sec), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
