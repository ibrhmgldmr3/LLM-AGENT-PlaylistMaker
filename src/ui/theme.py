THEMES = {
    "light": {
        "label": "Paper",
        "variables": {
            "app-bg": "#f6f1e8",
            "hero-bg": "linear-gradient(135deg, #19324d 0%, #274d62 48%, #b4783e 100%)",
            "surface": "#fffdfa",
            "surface-alt": "#efe5d6",
            "border": "#ccbba0",
            "text-primary": "#172231",
            "text-secondary": "#4f5f72",
            "hero-text": "#fffaf2",
            "hero-muted": "#f5e8d4",
            "accent": "#bf672c",
            "accent-strong": "#974b1f",
            "input-bg": "#fffaf3",
            "sidebar-bg": "#fbf7f0",
        },
    },
    "dark": {
        "label": "Studio",
        "variables": {
            "app-bg": "#10161f",
            "hero-bg": "linear-gradient(135deg, #1e2e3f 0%, #30596f 50%, #bd6d2d 100%)",
            "surface": "#18212d",
            "surface-alt": "#223041",
            "border": "#314155",
            "text-primary": "#f4efe6",
            "text-secondary": "#c1cad5",
            "hero-text": "#fff5ea",
            "hero-muted": "#e9d7c0",
            "accent": "#e78a43",
            "accent-strong": "#f1a566",
            "input-bg": "#111926",
            "sidebar-bg": "#141d29",
        },
    },
}


def theme_css(theme_key: str) -> str:
    theme = THEMES.get(theme_key, THEMES["light"])
    variables = "\n".join([f"--{k}: {v};" for k, v in theme["variables"].items()])
    return f"""
    <style>
    :root {{
        {variables}
    }}
    html, body, [data-testid="stAppViewContainer"] {{
        background: var(--app-bg) !important;
        color: var(--text-primary) !important;
        font-family: "Segoe UI Variable", "Trebuchet MS", sans-serif;
    }}
    .main .block-container {{
        max-width: 1180px;
        padding: 2.2rem 1.25rem 3rem;
    }}
    .hero {{
        background: var(--hero-bg);
        color: var(--hero-text);
        border-radius: 28px;
        padding: 1rem;
        margin-bottom: 1.4rem;
        box-shadow: 0 20px 45px rgba(35, 29, 17, 0.16);
    }}
    .hero__content {{
        max-width: 760px;
        padding: 1.35rem 1.45rem 1.45rem;
        border-radius: 22px;
        background: linear-gradient(180deg, rgba(8, 13, 20, 0.18), rgba(8, 13, 20, 0.28));
        backdrop-filter: blur(4px);
    }}
    .eyebrow {{
        letter-spacing: 0.14em;
        text-transform: uppercase;
        font-size: 0.78rem;
        color: var(--hero-muted);
        opacity: 0.95;
        margin-bottom: 0.35rem;
    }}
    .hero h1 {{
        margin: 0;
        color: var(--hero-text);
        line-height: 1.08;
        font-size: clamp(2rem, 4vw, 3.45rem);
        font-family: "Trebuchet MS", "Segoe UI Variable", sans-serif;
        text-wrap: balance;
    }}
    .hero .subtle {{
        margin-top: 0.8rem;
        margin-bottom: 0;
        color: var(--hero-muted);
        font-size: 1.02rem;
        line-height: 1.6;
    }}
    .card {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 1.1rem 1.25rem;
        margin: 0.95rem 0;
        box-shadow: 0 10px 26px rgba(31, 25, 17, 0.05);
    }}
    .panel {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 22px;
        padding: 1.1rem 1.25rem 1.25rem;
        margin: 0 0 1.35rem;
        box-shadow: 0 12px 30px rgba(31, 25, 17, 0.05);
    }}
    .panel__title {{
        margin: 0 0 0.25rem;
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--text-primary);
    }}
    .panel__copy {{
        margin: 0 0 0.95rem;
        color: var(--text-secondary);
        line-height: 1.55;
    }}
    .subtle {{
        color: var(--text-secondary);
    }}
    [data-testid="stSidebar"] {{
        background: var(--sidebar-bg);
        border-right: 1px solid rgba(0, 0, 0, 0.05);
    }}
    [data-testid="stSidebar"] .block-container {{
        padding-top: 1.5rem;
    }}
    [data-testid="stSidebar"] h2 {{
        color: var(--text-primary);
        font-size: 1.1rem;
        margin-bottom: 1rem;
    }}
    [data-testid="stWidgetLabel"] p,
    .stCheckbox label p,
    .stSelectbox label p,
    .stSlider label p,
    .stTextInput label p {{
        color: var(--text-primary) !important;
        font-weight: 600;
        letter-spacing: 0.01em;
    }}
    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea {{
        background: var(--input-bg) !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border) !important;
        border-radius: 14px !important;
    }}
    .stTextInput input::placeholder,
    .stTextArea textarea::placeholder {{
        color: var(--text-secondary) !important;
        opacity: 0.75 !important;
    }}
    .stSelectbox [data-baseweb="select"] > div {{
        background: var(--input-bg) !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border) !important;
        border-radius: 14px !important;
        min-height: 46px;
    }}
    .stCheckbox {{
        padding-top: 0.25rem;
    }}
    .stSlider [data-baseweb="slider"] {{
        padding-top: 0.35rem;
        padding-bottom: 0.1rem;
    }}
    .stButton > button, .stDownloadButton > button {{
        background: var(--accent) !important;
        color: #fff !important;
        border-radius: 999px !important;
        border: 0 !important;
        min-height: 48px !important;
        font-weight: 700 !important;
        letter-spacing: 0.01em;
    }}
    .stButton > button:hover, .stDownloadButton > button:hover {{
        background: var(--accent-strong) !important;
    }}
    .stLinkButton a {{
        border-radius: 999px !important;
        border: 1px solid var(--border) !important;
        background: var(--surface-alt) !important;
        color: var(--text-primary) !important;
    }}
    .stAlert {{
        border-radius: 16px !important;
    }}
    @media (max-width: 900px) {{
        .main .block-container {{
            padding: 1rem 0.8rem 2rem;
        }}
        .hero {{
            border-radius: 22px;
        }}
        .hero__content {{
            padding: 1rem 1rem 1.1rem;
        }}
        .panel {{
            padding: 0.95rem 1rem 1rem;
        }}
    }}
    </style>
    """
