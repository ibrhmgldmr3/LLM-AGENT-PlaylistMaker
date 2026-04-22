THEMES = {
    "light": {
        "label": "Light",
        "variables": {
            "app-bg": "#f5f7fb",
            "surface": "#ffffff",
            "surface-alt": "#eef2f7",
            "border": "#d7dee8",
            "text-primary": "#0f172a",
            "text-secondary": "#334155",
            "accent": "#2563eb",
            "accent-strong": "#1d4ed8",
        },
    },
    "dark": {
        "label": "Dark",
        "variables": {
            "app-bg": "#0f172a",
            "surface": "#1e293b",
            "surface-alt": "#273449",
            "border": "#334155",
            "text-primary": "#f1f5f9",
            "text-secondary": "#cbd5e1",
            "accent": "#60a5fa",
            "accent-strong": "#3b82f6",
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
    }}
    .main .block-container {{
        max-width: 1100px;
        padding: 2rem 1rem;
    }}
    .card {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 1rem 1.25rem;
        margin: 0.75rem 0;
    }}
    .subtle {{
        color: var(--text-secondary);
    }}
    .stButton > button {{
        background: var(--accent) !important;
        color: #fff !important;
        border-radius: 8px !important;
    }}
    .stButton > button:hover {{
        background: var(--accent-strong) !important;
    }}
    </style>
    """
