import streamlit as st

from src.config import load_config
from src.services.playlist_service import build_playlist
from src.ui.theme import THEMES, theme_css
from src.ui.components import header, video_card


st.set_page_config(page_title="Make A Playlist", layout="wide")

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"

st.markdown(theme_css(st.session_state["theme"]), unsafe_allow_html=True)

with st.sidebar:
    st.markdown("## Theme")
    theme_key = st.selectbox(
        "Theme",
        options=list(THEMES.keys()),
        index=list(THEMES.keys()).index(st.session_state["theme"]),
        format_func=lambda k: THEMES[k]["label"],
    )
    if theme_key != st.session_state["theme"]:
        st.session_state["theme"] = theme_key
        st.rerun()

header("Make A Playlist", "Generate a learning playlist from YouTube videos.")

try:
    config = load_config()
except RuntimeError as exc:
    st.error(str(exc))
    st.info("Please update .env with GEMINI_API_KEY and GEMINI_MODEL. See .env.example.")
    st.stop()

subject = st.text_input("Topic", placeholder="Example: Machine Learning, Python Programming...")

if st.button("Build Playlist", type="primary"):
    if not subject:
        st.warning("Please enter a topic.")
    else:
        progress = st.progress(0)
        status = st.empty()

        def on_progress(evt):
            progress.progress(evt.progress)
            status.text(evt.message)

        try:
            result = build_playlist(config, subject, progress_callback=on_progress)
            st.session_state["result"] = result
        except Exception as exc:
            st.error(f"Playlist could not be built: {exc}")
        finally:
            progress.empty()
            status.empty()

if "result" in st.session_state:
    result = st.session_state["result"]
    st.markdown("## Results")
    st.caption(f"Run ID: {result.run_id}")

    if result.warnings:
        for w in result.warnings:
            st.warning(w)

    if result.selected_videos:
        for v in result.selected_videos:
            video_card(v.title, v.url, 0, "")
    else:
        st.info("No videos selected yet.")

    st.markdown("## Details")
    for sub in result.subtopics:
        st.markdown(f"### {sub.subtopic}")
        st.caption(f"Candidates: {sub.candidates_considered} | Scored: {sub.top_scored}")
        for s in sub.scores:
            st.json(s)
