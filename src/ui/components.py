from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.models import PlaylistResult, Recommendation, SubtopicResult


def header(title: str, subtitle: str) -> None:
    st.markdown("<div class='hero'>", unsafe_allow_html=True)
    st.markdown("<div class='hero__content'>", unsafe_allow_html=True)
    st.markdown(f"<p class='eyebrow'>Learning Playlist Generator</p>", unsafe_allow_html=True)
    st.markdown(f"<h1>{title}</h1>", unsafe_allow_html=True)
    st.markdown(f"<p class='subtle'>{subtitle}</p>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def recommendation_card(recommendation: Recommendation) -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown(f"### {recommendation.position}. {recommendation.video.title}")
    st.caption(
        f"Subtopic: {recommendation.subtopic} | Confidence: {recommendation.confidence_score} | "
        f"Transcript: {recommendation.transcript_status}"
    )
    st.write(recommendation.why_selected)
    if recommendation.video.channel:
        st.write(f"Channel: {recommendation.video.channel}")
    if recommendation.video.duration_sec:
        minutes = round(recommendation.video.duration_sec / 60, 1)
        st.write(f"Duration: {minutes} min")
    st.link_button("Open Video", recommendation.video.url)
    with st.expander("Metadata breakdown"):
        st.json(recommendation.metadata_score.model_dump())
    st.markdown("</div>", unsafe_allow_html=True)


def subtopic_section(result: SubtopicResult) -> None:
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
        )


def export_buttons(result: PlaylistResult) -> None:
    if not result.exports:
        return
    json_path = Path(result.exports.json_path)
    markdown_path = Path(result.exports.markdown_path)
    if json_path.exists():
        st.download_button(
            "Download JSON",
            data=json_path.read_text(encoding="utf-8"),
            file_name=json_path.name,
            mime="application/json",
        )
    if markdown_path.exists():
        st.download_button(
            "Download Study Plan",
            data=markdown_path.read_text(encoding="utf-8"),
            file_name=markdown_path.name,
            mime="text/markdown",
        )
