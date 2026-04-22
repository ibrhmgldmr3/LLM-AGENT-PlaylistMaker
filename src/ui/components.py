import streamlit as st


def header(title: str, subtitle: str) -> None:
    st.markdown(f"# {title}")
    st.markdown(f"<p class='subtle'>{subtitle}</p>", unsafe_allow_html=True)


def video_card(title: str, url: str, score: int, comment: str) -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown(f"**{title}**")
    st.video(url)
    st.metric("Score", f"{score}/10")
    if comment:
        st.caption(comment)
    st.markdown("</div>", unsafe_allow_html=True)
