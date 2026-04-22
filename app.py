import os

if os.name == "nt":
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import streamlit as st

from src.config import load_config
from src.models import FilterOptions, PlaylistRequest
from src.services.playlist_service import build_playlist
from src.ui.components import export_buttons, header, recommendation_card, subtopic_section
from src.ui.theme import THEMES, theme_css


st.set_page_config(page_title="Make A Playlist", layout="wide")

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"

st.markdown(theme_css(st.session_state["theme"]), unsafe_allow_html=True)

try:
    config = load_config()
except RuntimeError as exc:
    st.error(str(exc))
    st.info("Update `.env` with at least `GEMINI_API_KEY`. See `.env.example`.")
    st.stop()

with st.sidebar:
    st.markdown("## Controls")
    theme_key = st.selectbox(
        "Theme",
        options=list(THEMES.keys()),
        index=list(THEMES.keys()).index(st.session_state["theme"]),
        format_func=lambda key: THEMES[key]["label"],
    )
    if theme_key != st.session_state["theme"]:
        st.session_state["theme"] = theme_key
        st.rerun()

    language = st.selectbox("Language", ["en", "tr"], index=0)
    difficulty = st.selectbox("Difficulty", ["mixed", "beginner", "intermediate", "advanced"], index=0)
    max_duration_minutes = st.slider("Max duration (minutes)", min_value=10, max_value=180, value=60, step=5)
    freshness_preference = st.selectbox("Freshness", ["balanced", "evergreen", "recent"], index=0)
    create_youtube_playlist = st.checkbox(
        "Create official YouTube playlist",
        value=False,
        help="Requires YouTube Data API credentials and OAuth client secrets.",
    )

header(
    "YouTube Playlist Generator",
    "Metadata-first ranking, transcript fallback, SQLite cache, and optional official YouTube playlist publishing.",
)

st.markdown(
    """
    <div class="panel">
        <p class="panel__title">Build a playlist</p>
        <p class="panel__copy">
            Enter one learning goal. The app will split it into subtopics, collect broader candidate pools,
            rank videos by metadata first, and only use transcripts as optional enrichment.
        </p>
    """,
    unsafe_allow_html=True,
)
topic = st.text_input("Topic", placeholder="Example: Applied machine learning for tabular data")
st.caption("Tip: clearer, narrower topics usually produce better subtopic planning and ranking.")
build_clicked = st.button("Build Playlist", type="primary", use_container_width=True)
st.markdown("</div>", unsafe_allow_html=True)

if build_clicked:
    if not topic.strip():
        st.warning("Enter a topic before building a playlist.")
    else:
        filters = FilterOptions(
            language=language,
            difficulty=difficulty,
            max_duration_minutes=max_duration_minutes,
            freshness_preference=freshness_preference,
        )
        request = PlaylistRequest(
            topic=topic.strip(),
            filters=filters,
            create_youtube_playlist=create_youtube_playlist,
        )
        progress_bar = st.progress(0)
        status_box = st.empty()

        def on_progress(event):
            progress_bar.progress(event.progress)
            label = event.stage.replace("_", " ").title()
            status_box.info(f"{label}: {event.message}")

        try:
            result = build_playlist(config, request, progress_callback=on_progress)
            st.session_state["result"] = result
        except Exception as exc:
            st.error("Playlist generation failed.")
            st.caption(str(exc))
        finally:
            progress_bar.empty()
            status_box.empty()

if "result" in st.session_state:
    result = st.session_state["result"]
    st.markdown("## Playlist")
    st.caption(f"Run ID: {result.run_id}")

    if result.warnings:
        for warning in result.warnings:
            st.warning(warning)

    if result.published_playlist_url:
        st.success("Official YouTube playlist created.")
        st.link_button("Open Playlist", result.published_playlist_url)

    if result.recommendations:
        for recommendation in result.recommendations:
            recommendation_card(recommendation)
    else:
        st.info("No recommendations were produced for the current filters.")

    st.markdown("## Exports")
    export_buttons(result)

    st.markdown("## Per-Subtopic Diagnostics")
    for item in result.subtopics:
        subtopic_section(item)
