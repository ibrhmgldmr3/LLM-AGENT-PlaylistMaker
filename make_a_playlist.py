# -*- coding: utf-8 -*-

import streamlit as st
import json
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from playlist_maker.transkript_analizi import tum_transkriptleri_analiz_et, tum_islemler
from playlist_maker.video_ayiklayici import youtubede_ara
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

# Streamlit sayfa konfigürasyonu
st.set_page_config(
    page_title="📚 Akıllı Öğrenme Asistanı",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# TEMA TANIMLARI
THEMES = {
    "light": {
        "label": "Varsayılan (Açık)",
        "variables": {
            "app-bg": "#f8fafc",
            "surface": "#ffffff",
            "surface-alt": "#f1f5f9",
            "surface-muted": "#e2e8f0",
            "border": "#cbd5e1",
            "border-strong": "#94a3b8",
            "text-primary": "#0f172a",
            "text-secondary": "#334155",
            "text-muted": "#64748b",
            "accent": "#3b82f6",
            "accent-strong": "#2563eb",
            "accent-soft": "rgba(59, 130, 246, 0.1)",
            "success": "#059669",
            "success-surface": "#ecfdf5",
            "warning": "#d97706",
            "warning-surface": "#fffbeb",
            "info": "#0284c7",
            "info-surface": "#e0f2fe",
            "shadow": "0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06)",
            "shadow-hover": "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
        },
    },
    "dark": {
        "label": "Koyu Mod",
        "variables": {
            "app-bg": "#0f172a",
            "surface": "#1e293b",
            "surface-alt": "#334155",
            "surface-muted": "#475569",
            "border": "#475569",
            "border-strong": "#64748b",
            "text-primary": "#f1f5f9",
            "text-secondary": "#cbd5e1",
            "text-muted": "#94a3b8",
            "accent": "#60a5fa",
            "accent-strong": "#3b82f6",
            "accent-soft": "rgba(96, 165, 250, 0.2)",
            "success": "#10b981",
            "success-surface": "rgba(16, 185, 129, 0.1)",
            "warning": "#f59e0b",
            "warning-surface": "rgba(245, 158, 11, 0.1)",
            "info": "#06b6d4",
            "info-surface": "rgba(6, 182, 212, 0.1)",
            "shadow": "0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -1px rgba(0, 0, 0, 0.3)",
            "shadow-hover": "0 20px 25px -5px rgba(0, 0, 0, 0.4), 0 10px 10px -5px rgba(0, 0, 0, 0.3)",
        },
    },
}

THEME_SEQUENCE = list(THEMES.keys())

# Session state başlatma
if "theme" not in st.session_state:
    st.session_state["theme"] = "light"

def inject_theme_css():
    """Temayı CSS olarak enjekte et"""
    theme = THEMES.get(st.session_state["theme"], THEMES["light"])
    variables = "\n        ".join(
        f"--{name}: {value};" for name, value in theme["variables"].items()
    )
    
    css = f"""
    <style>
    :root {{
        {variables}
    }}
    
    /* GENEL SIFIRLAMA */
    *, *::before, *::after {{
        box-sizing: border-box;
    }}
    
    /* ANA LAYOUT */
    html, body, [data-testid="stAppViewContainer"] {{
        background: var(--app-bg) !important;
        color: var(--text-primary) !important;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    }}
    
    .main .block-container {{
        padding: 2rem 1rem;
        max-width: 1200px;
        margin: 0 auto;
    }}
    
    /* BASLIKLAR */
    .main-header {{
        text-align: center;
        font-size: 2.5rem;
        font-weight: 700;
        color: var(--text-primary);
        margin-bottom: 0.5rem;
    }}
    
    .sub-header {{
        text-align: center;
        color: var(--text-secondary);
        font-size: 1.1rem;
        margin-bottom: 2rem;
        max-width: 600px;
        margin-left: auto;
        margin-right: auto;
    }}
    
    /* FORM ELEMANLARI */
    .stTextInput input, .stTextArea textarea {{
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        color: var(--text-primary) !important;
        padding: 0.75rem !important;
        font-size: 1rem !important;
        transition: all 0.2s ease !important;
    }}
    
    .stTextInput input:focus, .stTextArea textarea:focus {{
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-soft) !important;
        outline: none !important;
    }}
    
    .stTextInput input::placeholder, .stTextArea textarea::placeholder {{
        color: var(--text-muted) !important;
    }}
    
    /* SELECTBOX */
    [data-baseweb="select"] > div {{
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        color: var(--text-primary) !important;
    }}
    
    [data-baseweb="select"]:focus-within > div {{
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-soft) !important;
    }}
    
    /* BUTONLAR */
    .stButton > button {{
        background: var(--accent) !important;
        color: white !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 0.75rem 1.5rem !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
        box-shadow: var(--shadow) !important;
    }}
    
    .stButton > button:hover {{
        background: var(--accent-strong) !important;
        transform: translateY(-1px) !important;
        box-shadow: var(--shadow-hover) !important;
    }}
    
    .stButton > button:disabled {{
        background: var(--surface-muted) !important;
        color: var(--text-muted) !important;
        transform: none !important;
        box-shadow: none !important;
    }}
    
    /* SIDEBAR */
    section[data-testid="stSidebar"] {{
        background: var(--surface) !important;
        border-right: 1px solid var(--border) !important;
    }}
    
    section[data-testid="stSidebar"] > div {{
        padding: 1.5rem !important;
    }}
    
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {{
        color: var(--text-primary) !important;
    }}
    
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] li {{
        color: var(--text-secondary) !important;
    }}
    
    /* KARTLAR */
    .card {{
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
        padding: 1.5rem !important;
        margin: 1rem 0 !important;
        box-shadow: var(--shadow) !important;
        transition: all 0.2s ease !important;
    }}
    
    .card:hover {{
        transform: translateY(-2px) !important;
        box-shadow: var(--shadow-hover) !important;
    }}
    
    .topic-card {{
        background: var(--surface-alt) !important;
        border: 1px solid var(--border) !important;
        border-left: 4px solid var(--accent) !important;
        border-radius: 8px !important;
        padding: 1rem !important;
        margin: 0.5rem 0 !important;
    }}
    
    .video-card {{
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
        padding: 1.5rem !important;
        margin: 1rem 0 !important;
        box-shadow: var(--shadow) !important;
        transition: all 0.2s ease !important;
    }}
    
    .video-card:hover {{
        transform: translateY(-2px) !important;
        box-shadow: var(--shadow-hover) !important;
    }}
    
    /* ALERT MESAJLARI */
    .success-message {{
        background: var(--success-surface) !important;
        border: 1px solid var(--success) !important;
        border-radius: 8px !important;
        padding: 1rem !important;
        color: var(--success) !important;
    }}
    
    .alert-info {{
        background: var(--info-surface) !important;
        border: 1px solid var(--info) !important;
        border-radius: 8px !important;
        padding: 1rem !important;
        color: var(--info) !important;
    }}
    
    .alert-warning {{
        background: var(--warning-surface) !important;
        border: 1px solid var(--warning) !important;
        border-radius: 8px !important;
        padding: 1rem !important;
        color: var(--warning) !important;
    }}
    
    /* METRIKLER */
    [data-testid="metric-container"] {{
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        padding: 1rem !important;
        box-shadow: var(--shadow) !important;
    }}
    
    [data-testid="metric-container"] [data-testid="stMetricLabel"] {{
        color: var(--text-secondary) !important;
        font-weight: 600 !important;
    }}
    
    [data-testid="metric-container"] [data-testid="stMetricValue"] {{
        color: var(--text-primary) !important;
        font-weight: 700 !important;
        font-size: 1.5rem !important;
    }}
    
    [data-testid="metric-container"] [data-testid="stMetricDelta"] {{
        color: var(--accent) !important;
        font-weight: 600 !important;
    }}
    
    /* TABLAR */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 0.5rem;
        border-bottom: 1px solid var(--border);
    }}
    
    .stTabs [data-baseweb="tab"] {{
        background: transparent !important;
        color: var(--text-secondary) !important;
        border-radius: 6px !important;
        padding: 0.5rem 1rem !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
    }}
    
    .stTabs [data-baseweb="tab"]:hover {{
        background: var(--surface-alt) !important;
        color: var(--text-primary) !important;
    }}
    
    .stTabs [aria-selected="true"] {{
        background: var(--accent-soft) !important;
        color: var(--accent-strong) !important;
        font-weight: 600 !important;
    }}
    
    /* PROGRESS BAR */
    .stProgress > div > div > div {{
        background: var(--surface-muted) !important;
    }}
    
    .stProgress > div > div > div > div {{
        background: var(--accent) !important;
    }}
    
    /* EXPANDER */
    .streamlit-expanderHeader {{
        background: var(--surface-alt) !important;
        color: var(--text-primary) !important;
        border-radius: 6px !important;
        padding: 0.5rem 0.75rem !important;
    }}
    
    /* VIDEO CONTAINER */
    .video-container {{
        border-radius: 8px !important;
        overflow: hidden !important;
        box-shadow: var(--shadow) !important;
        margin: 1rem 0 !important;
    }}
    
    /* RESPONSIVE */
    @media (max-width: 768px) {{
        .main .block-container {{
            padding: 1rem 0.5rem;
        }}
        
        .main-header {{
            font-size: 2rem;
        }}
        
        .sub-header {{
            font-size: 1rem;
            margin-bottom: 1.5rem;
        }}
        
        .card, .video-card {{
            padding: 1rem;
        }}
    }}
    
    /* ANIMASYONLAR */
    @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(10px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    
    .card, .video-card, .topic-card {{
        animation: fadeIn 0.3s ease-out;
    }}
    
    /* SCROLLBAR */
    ::-webkit-scrollbar {{
        width: 8px;
    }}
    
    ::-webkit-scrollbar-track {{
        background: var(--surface-muted);
    }}
    
    ::-webkit-scrollbar-thumb {{
        background: var(--border-strong);
        border-radius: 4px;
    }}
    
    ::-webkit-scrollbar-thumb:hover {{
        background: var(--text-muted);
    }}
    </style>
    """
    
    st.markdown(css, unsafe_allow_html=True)

def format_theme_option(option: str) -> str:
    return THEMES[option]["label"]

# Tema CSS'ini uygula
inject_theme_css()

SUMMARY_METRIC_CSS = """
<style>
.summary-grid {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 1.4rem 1.6rem;
    box-shadow: var(--shadow-hover);
    margin: 1.5rem 0;
}

.summary-grid .summary-heading {
    margin: 0 0 0.75rem;
    color: var(--text-primary);
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: 0.01em;
}

.summary-grid [data-testid="column"] {
    align-items: stretch;
}

.summary-grid [data-testid="column"] > div {
    display: flex;
    justify-content: center;
}

.summary-grid [data-testid="metric-container"] {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 1.15rem 1.3rem;
    box-shadow: var(--shadow);
}

.summary-grid [data-testid="stMetricLabel"] {
    color: var(--text-secondary) !important;
    font-weight: 600;
    letter-spacing: 0.02em;
}

.summary-grid [data-testid="stMetricValue"] {
    color: var(--text-primary) !important;
    font-weight: 700;
    font-size: 1.6rem;
}

.summary-grid [data-testid="stMetricDelta"] {
    color: var(--accent-strong) !important;
    font-weight: 600;
}

.summary-grid [data-testid="stMetricDelta"] svg {
    fill: var(--accent-strong);
}

/* Light tema için İstatistikler başlığı ve metric'ler */
.summary-heading {
    color: #0f172a !important;
    font-weight: 700 !important;
}

[data-testid="metric-container"] {
    background: white !important;
    border: 1px solid #cbd5e1 !important;
}

[data-testid="metric-container"] [data-testid="stMetricLabel"] {
    color: #0f172a !important;
    font-weight: 700 !important;
}

[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #0f172a !important;
    font-weight: 900 !important;
}

[data-testid="metric-container"] [data-testid="stMetricDelta"] {
    color: #374151 !important;
    font-weight: 600 !important;
}

/* Dark tema için metric'ler */
[data-theme="dark"] [data-testid="metric-container"] {
    background: #111827 !important;
    border: 1px solid #374151 !important;
}

[data-theme="dark"] [data-testid="metric-container"] [data-testid="stMetricLabel"] {
    color: #e2e8f0 !important;
}

[data-theme="dark"] [data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #e2e8f0 !important;
}

[data-theme="dark"] [data-testid="metric-container"] [data-testid="stMetricDelta"] {
    color: #cbd5f5 !important;
}

[data-theme="dark"] .summary-heading {
    color: #e2e8f0 !important;
}
</style>
"""
st.markdown(SUMMARY_METRIC_CSS, unsafe_allow_html=True)


# Environment variables
os.environ["STREAMLIT_DISABLE_WATCHDOG_WARNINGS"] = "true"
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_API_URL")

# Modern başlık
st.markdown('<h1 class="main-header">🎓 Akıllı Öğrenme Asistanı</h1>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Konuları keşfedin, öğrenin ve uzmanlaşın - AI destekli kişiselleştirilmiş eğitim deneyimi</p>', unsafe_allow_html=True)

# LLM setup
llm = ChatOpenAI(
    model="openai/gpt-oss-20b:free",
    openai_api_key=api_key,
    base_url=base_url,
    temperature=0.3,
    max_tokens=1500,
)

def altbasliklari_cikar(konu):
    try:
        # Yeni konu için analiz sonuçlarını sıfırla
        analiz_dosyasi = "analiz_sonuclari.json"
        try:
            with open(analiz_dosyasi, "w", encoding="utf-8") as f:
                json.dump({
                    "konu": konu,
                    "analiz_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "alt_basliklar": []
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            pass  # Sessizce geç
        
        prompt_template = ChatPromptTemplate.from_template("""Aşağıdaki konu başlığına göre öğrenilmesi gereken alt başlıkları liste olarak oluştur: {konu}
Sadece başlıkları listele ve numarasız şekilde JSON listesi olarak ver, açıklama ve yorum yapma!!!
["Alt başlık 1", "Alt başlık 2", ...] formatında yanıtla.""")
        messages = prompt_template.format_messages(konu=konu)
        result = llm.invoke(messages)
        raw_response = result.content.strip()

        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            lines = raw_response.splitlines()
            return [line.strip("0123456789. ").strip() for line in lines if line.strip()]
    except Exception as e:
        st.error(f"Alt başlıkları oluştururken hata oluştu: {str(e)}")
        return []

def youtube_transkript_al(video_url):
    """YouTube videosundan mevcut transkripti alır (orijinal dil veya İngilizce)"""
    try:
        # Video ID'sini çıkar
        if "watch?v=" in video_url:
            video_id = video_url.split("watch?v=")[1].split("&")[0]
        elif "youtu.be/" in video_url:
            video_id = video_url.split("youtu.be/")[1].split("?")[0]
        else:
            return None
        
        # YouTubeTranscriptApi instance'ı oluştur
        api = YouTubeTranscriptApi()
        
        # Önce direkt transkript almayı dene (en basit yol)
        try:
            # Türkçe transkript dene
            transcript_data = api.fetch(video_id, languages=['tr'])
            transcript_text = ' '.join([item['text'] for item in transcript_data])
            return {
                'metin': transcript_text,
                'dil': 'tr',
                'tip': 'youtube_transcript'
            }
        except Exception as e:
            # IP bloke hatası varsa özel mesaj
            if "IpBlocked" in str(e) or "ip" in str(e).lower():
                st.warning(f"YouTube API IP bloğu nedeniyle transkript alınamadı. Alternatif yöntem denenecek...")
                return None
            try:
                # İngilizce transkript dene  
                transcript_data = api.fetch(video_id, languages=['en'])
                transcript_text = ' '.join([item['text'] for item in transcript_data])
                return {
                    'metin': transcript_text,
                    'dil': 'en', 
                    'tip': 'youtube_transcript'
                }
            except Exception as e2:
                if "IpBlocked" in str(e2) or "ip" in str(e2).lower():
                    st.warning(f"YouTube API IP bloğu nedeniyle transkript alınamadı. Alternatif yöntem denenecek...")
                else:
                    st.warning(f"Video {video_id} için YouTube transkripti alınamadı: {e2}")
                return None
                
    except Exception as e:
        st.error(f"YouTube transkript alma hatası: {e}")
        return None

def alt_basliklar_icin_videolar(altbasliklar):
    try:
        cikti_dosyasi = "en_iyi_video.txt"
        video_linkleri_yolu = Path(cikti_dosyasi)
        video_linkleri_yolu.parent.mkdir(parents=True, exist_ok=True)

        with open(video_linkleri_yolu, "w", encoding="utf-8") as f:
            f.write("")

        for altbaslik in altbasliklar:
            cikti_dosyasi = "playlist_maker/datas/video_linkleri.txt"
            search_success = youtubede_ara(konu + " " + altbaslik, output_file=cikti_dosyasi, num_results=2)
            if not search_success:
                continue

            video_linkleri_yolu = Path(cikti_dosyasi)
            if not video_linkleri_yolu.exists() or video_linkleri_yolu.stat().st_size == 0:
                continue

            with open(video_linkleri_yolu, 'r', encoding='utf-8') as f:
                video_links = [line.strip() for line in f if line.strip()]

            for link in video_links:
                try:
                    # Ses transkriptleri modülündeki gelişmiş transkript fonksiyonunu kullan
                    from playlist_maker.ses_transkriptleri import transkriptle
                    
                    transcript_data = transkriptle(link, "playlist_maker/datas/ses_transkriptleri")
                    
                    # Sessizce devam et - kullanıcıya bilgi verme
                        
                except Exception as e:
                    continue

            tum_islemler(altbaslik)

        st.session_state["videolar_yuklendi"] = True
        # Sessizce devam et - kullanıcıya bilgi verme
        st.rerun()
    except Exception as e:
        st.error(f"Video arama sırasında hata oluştu: {str(e)}")
        return False

def en_iyi_videolari_yukle():
    try:
        with open("en_iyi_video.txt", "r", encoding="utf-8") as file:
            return [line.strip() for line in file if line.strip()]
    except Exception as e:
        st.error(f"En iyi videolar okunurken hata oluştu: {str(e)}")
        return []

# Ana içerik alanı
col1, col2, col3 = st.columns([1, 2, 1])

with col2:
    st.markdown("### 🎯 Öğrenmek İstediğiniz Konuyu Girin")
    
    # Modern input alanı
    konu = st.text_input(
        "Konu Girin",
        placeholder="Örnek: Python Programlama, Makine Öğrenmesi, Dijital Pazarlama...",
        help="Öğrenmek istediğiniz herhangi bir konuyu yazabilirsiniz",
        label_visibility="collapsed"
    )
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Modern buton
    if st.button("🔍 İçerikleri Keşfet", use_container_width=True):
        if konu:
            with st.spinner("🔍 AI ile alt başlıklar analiz ediliyor..."):
                altbasliklar = altbasliklari_cikar(konu)
                if altbasliklar:
                    st.session_state["konu"] = konu
                    st.session_state["altbasliklar"] = altbasliklar
                    st.rerun()
                else:
                    # Sessizce devam et - hata kullanıcıya gösterilmez
                    pass
        else:
            st.warning("⚠️ Lütfen önce bir konu girin!")

# Alt başlıkları göster
if "altbasliklar" in st.session_state and "konu" in st.session_state:
    st.markdown("---")
    
    # Başlık
    st.markdown(f"### 🧭 **{st.session_state.konu}** Öğrenme Yol Haritası")
    
    # Alt başlıkları modern kartlarda göster
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.markdown("#### 🎯 Öğrenilecek Konular")
        
        for i, altbaslik in enumerate(st.session_state.altbasliklar, 1):
            st.markdown(f"""
            <div class="topic-card">
                <h4 style="margin: 0; color: var(--accent-strong);">📌 {i}. {altbaslik}</h4>
            </div>
            """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("<h4 class=\"summary-heading\">⚡ İstatistikler</h4>", unsafe_allow_html=True)
        
        # Metrik kartları
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("📋 Toplam Konu", len(st.session_state.altbasliklar))
        with col_b:
            st.metric("⏱️ Tahmini Süre", f"{len(st.session_state.altbasliklar) * 15} dk")

        st.markdown("<br>", unsafe_allow_html=True)
        
        # Video arama butonu
        if st.button("🎥 Öğrenme Videoları Bul", use_container_width=True, type="primary"):
            with st.spinner("🔍 En iyi eğitim videoları aranıyor..."):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                total_topics = len(st.session_state.altbasliklar)
                for i, altbaslik in enumerate(st.session_state.altbasliklar):
                    progress = (i + 1) / total_topics
                    progress_bar.progress(progress)
                    status_text.text(f"📺 '{altbaslik}' için videolar aranıyor... ({i+1}/{total_topics})")
                
                alt_basliklar_icin_videolar(st.session_state.altbasliklar)
                progress_bar.empty()
                status_text.empty()

# Video sonuçları
if st.session_state.get("videolar_yuklendi"):
    st.markdown("---")
    
    # Analiz sonuçlarını yükle
    analiz_verileri = None
    try:
        with open("analiz_sonuclari.json", "r", encoding="utf-8") as file:
            analiz_verileri = json.load(file)
    except:
        pass
    
    best_videos = en_iyi_videolari_yukle()
    
    if best_videos:
        # Başarı mesajı
        st.balloons()
        st.markdown("""
        <div class="success-message">
            <h4 style="margin: 0;">🎉 Harika! En iyi eğitim videoları bulundu ve AI ile analiz edildi!</h4>
        </div>
        """, unsafe_allow_html=True)

        # İstatistikler
        st.markdown('<div class="summary-grid">', unsafe_allow_html=True)
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("📹 Bulunan Video", len(best_videos), delta="Yeni")
        with col2:
            if analiz_verileri:
                total_analyzed = sum(len(ab.get("video_analizleri", [])) for ab in analiz_verileri.get("alt_basliklar", []))
                st.metric("🤖 Analiz Edilen", total_analyzed, delta="AI ile")
        with col3:
            st.metric("⭐ Kalite Skoru", "Yüksek", delta="Optimized")
        with col4:
            st.metric("⚡ Hız", "Çok Hızlı", delta="Real-time")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        # Ana başlık
        st.markdown("## 🎬 Kişiselleştirilmiş Öğrenme Videolarınız")
        
        # Konu bilgilerini göster
        if analiz_verileri:
            st.markdown(f"""
            <div class="alert-info">
                <h4 style="margin: 0 0 0.5rem 0;">📚 <strong>{analiz_verileri.get('konu', 'Bilinmiyor')}</strong></h4>
                <p style="margin: 0; opacity: 0.8;">📅 Analiz Tarihi: {analiz_verileri.get('analiz_tarihi', 'Bilinmiyor')}</p>
            </div>
            """, unsafe_allow_html=True)
        
        # Tablar kullanarak organize et
        if analiz_verileri and analiz_verileri.get("alt_basliklar"):
            tab_names = [f"📌 {ab['alt_baslik'][:20]}..." if len(ab['alt_baslik']) > 20 else f"📌 {ab['alt_baslik']}" for ab in analiz_verileri["alt_basliklar"]]
            tabs = st.tabs(tab_names)
            
            for tab_idx, (tab, alt_baslik_veri) in enumerate(zip(tabs, analiz_verileri["alt_basliklar"])):
                with tab:
                    st.markdown(f"### 🎯 {alt_baslik_veri['alt_baslik']}")
                    
                    video_analizleri = alt_baslik_veri.get("video_analizleri", [])
                    if not video_analizleri:
                        st.warning("❌ Bu alt başlık için analiz edilen video bulunamadı.")
                        continue
                    
                    # En yüksek puanlı videoyu bul
                    en_iyi_video = max(video_analizleri, key=lambda x: x.get("genel_puan", 0))
                    
                    # En iyi video vurgusu
                    if en_iyi_video:
                        st.markdown(f"""
                        <div class="success-message">
                            <h4 style="margin: 0;">🏆 En İyi Video Önerisi</h4>
                            <p style="margin: 0.5rem 0 0 0;">Genel Puan: <strong>{en_iyi_video.get('genel_puan', 0)}/10</strong></p>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    # Videoları responsive grid'de göster
                    for i in range(0, len(video_analizleri), 2):
                        video_pair = video_analizleri[i:i+2]
                        
                        if len(video_pair) == 2:
                            col1, col2 = st.columns(2)
                            columns = [col1, col2]
                        else:
                            col1, col2, col3 = st.columns([1, 2, 1])
                            columns = [col2]
                        
                        for j, video in enumerate(video_pair):
                            with columns[j]:
                                # Video kartı
                                st.markdown('<div class="video-card">', unsafe_allow_html=True)
                                
                                # En iyi video işareti
                                if video['video_id'] == en_iyi_video['video_id']:
                                    st.markdown("🏆 **EN İYİ SEÇİM**")
                                
                                # Video embed
                                with st.container():
                                    st.markdown('<div class="video-container">', unsafe_allow_html=True)
                                    st.video(video['video_url'])
                                    st.markdown('</div>', unsafe_allow_html=True)
                                
                                # Video bilgileri
                                st.markdown(f"**🎬 Video {i+j+1}**")
                                
                                # Genel puan - büyük ve belirgin
                                score_color = "🟢" if video.get('genel_puan', 0) >= 8 else "🟡" if video.get('genel_puan', 0) >= 6 else "🔴"
                                st.metric("🎯 AI Kalite Puanı", f"{video.get('genel_puan', 0)}/10", delta=f"{score_color}")
                                
                                # Yorum
                                if video.get('yorum'):
                                    st.markdown(f"💭 **AI Yorumu:** {video['yorum']}")
                                
                                # Video linkı
                                st.markdown(f"[🔗 Videoyu YouTube'da Aç]({video['video_url']})")
                                
                                # Detaylı skorları göster
                                with st.expander("📈 Detaylı AI Analizi"):
                                    score_data = {
                                        "🎯 Kapsam Uyumu": video.get('kapsam_uyumu', 0),
                                        "🧠 Bilgi Derinliği": video.get('bilgi_derinligi', 0),
                                        "🎤 Anlatım Tarzı": video.get('anlatim_tarzi', 0),
                                        "👥 Hedef Kitle": video.get('hedef_kitle', 0),
                                        "📋 Yapısal Tutarlılık": video.get('yapisal_tutarlilik', 0)
                                    }
                                    
                                    for metric_name, score in score_data.items():
                                        st.progress(score/10)
                                        st.caption(f"{metric_name}: {score}/10")
                                
                                st.markdown('</div>', unsafe_allow_html=True)
                    
                    # Sayfa sonu boşluk
                    st.markdown("<br>", unsafe_allow_html=True)
        else:
            # Analiz verisi yoksa sadece videoları göster
            st.markdown("### 🎥 Bulunan Videolar")
            
            # Videoları yan yana göstermek için kolonlar oluştur
            if len(best_videos) == 1:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    st.markdown('<div class="video-card">', unsafe_allow_html=True)
                    st.video(best_videos[0])
                    st.write("📹 **Önerilen Video**")
                    st.markdown('</div>', unsafe_allow_html=True)
            elif len(best_videos) == 2:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown('<div class="video-card">', unsafe_allow_html=True)
                    st.video(best_videos[0])
                    st.write("📹 **Video 1**")
                    st.markdown('</div>', unsafe_allow_html=True)
                with col2:
                    st.markdown('<div class="video-card">', unsafe_allow_html=True)
                    st.video(best_videos[1])
                    st.write("📹 **Video 2**")
                    st.markdown('</div>', unsafe_allow_html=True)
            else:
                video_chunks = [best_videos[i:i+3] for i in range(0, len(best_videos), 3)]
                
                for chunk_idx, video_chunk in enumerate(video_chunks):
                    if len(video_chunk) == 3:
                        col1, col2, col3 = st.columns(3)
                        columns = [col1, col2, col3]
                    elif len(video_chunk) == 2:
                        col1, col2 = st.columns(2)
                        columns = [col1, col2]
                    else:
                        col1, col2, col3 = st.columns([1, 2, 1])
                        columns = [col2]
                    
                    for i, video_url in enumerate(video_chunk):
                        video_number = chunk_idx * 3 + i + 1
                        with columns[i]:
                            st.markdown('<div class="video-card">', unsafe_allow_html=True)
                            st.video(video_url)
                            st.write(f"📹 **Video {video_number}**")
                            
                            if "watch?v=" in video_url:
                                video_id = video_url.split("watch?v=")[1].split("&")[0]
                                st.caption(f"ID: {video_id}")
                            st.markdown('</div>', unsafe_allow_html=True)
                    
                    if chunk_idx < len(video_chunks) - 1:
                        st.markdown("---")
    else:
        st.warning("⚠️ Hiçbir video bulunamadı.")

# Modern sidebar
with st.sidebar:
    st.markdown("# 🎯 Akıllı Öğrenme Hub")
    
    # Tema seçici - en üstte
    st.markdown("### 🎨 Tema")
    new_theme = st.selectbox(
        "Görünüm Seçin",
        options=THEME_SEQUENCE,
        index=THEME_SEQUENCE.index(st.session_state["theme"]),
        format_func=format_theme_option,
        key="theme_selector",
    )
    
    if new_theme != st.session_state["theme"]:
        st.session_state["theme"] = new_theme
        st.rerun()
    
    st.markdown("---")
    
    # Özellikler
    st.markdown("## ✨ Özellikler")
    st.markdown("""
    - 🤖 **AI Destekli Analiz** - GPT ile konu analizi
    - 🎬 **Akıllı Video Seçimi** - YouTube'dan en uygun içerikler  
    - 📊 **Kalite Puanlama** - Her video için detaylı skor
    - 🎯 **Kişiselleştirme** - Size özel öğrenme yolu
    """)
    
    # Nasıl çalışır
    st.markdown("## 🔄 Nasıl Çalışır?")
    with st.expander("Adım Adım Rehber", expanded=False):
        st.markdown("""
        **1. Konu Girin** 📝  
        Öğrenmek istediğiniz herhangi bir konuyu yazın
        
        **2. AI Analizi** 🧠  
        Yapay zeka konuyu alt başlıklara böler
        
        **3. Video Arama** 🔍  
        Her alt başlık için en iyi videoları bulur

        **4. Kalite Analizi** ⭐  
        Her video AI ile analiz edilip puanlanır
        
        **5. Kişisel Playlist** 🎵  
        Size özel öğrenme listesi hazırlanır
        """)
    
    # Destek
    st.markdown("---")
    st.markdown("## 💬 Destek & İletişim")
    
    # Social links
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("[![GitHub](https://img.shields.io/badge/-GitHub-black?style=flat&logo=github)](https://github.com/ibrhmgldmr3)")
    with col2:
        st.markdown("[![LinkedIn](https://img.shields.io/badge/-LinkedIn-blue?style=flat&logo=linkedin)](https://www.linkedin.com/in/ibrhmgldmr/)")

    # Hakkında
    st.markdown("---")
    st.markdown("### ℹ️ Hakkında")
    st.markdown("""
    **Akıllı Öğrenme Asistanı** yapay zeka teknolojisi ile 
    kişiselleştirilmiş eğitim deneyimi sunar.
    
    🚀 **Geliştirici:** [Ibrahim Güldemir](https://github.com/ibrhmgldmr3)  
    """)

# EMERGENCY CSS OVERRIDE - EN SON UYGULANAN
st.markdown("""
<style>
/* ULTIMATE OVERRIDE - İSTATİSTİKLER İÇİN */
.summary-heading,
h4.summary-heading,
[class*="summary-heading"] {
    color: #000000 !important;
    font-weight: 800 !important;
    opacity: 1 !important;
    text-shadow: none !important;
}

[data-testid="metric-container"] {
    background: #ffffff !important;
    border: 2px solid #cccccc !important;
}

[data-testid="metric-container"] *,
[data-testid="metric-container"] div,
[data-testid="metric-container"] span,
[data-testid="metric-container"] p {
    color: #000000 !important;
    opacity: 1 !important;
    text-shadow: none !important;
}

[data-testid="stMetricLabel"],
[data-testid="metric-container"] [data-testid="stMetricLabel"] {
    color: #BBBBBB !important;
    font-weight: 700 !important;
}

[data-testid="stMetricValue"], 
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #BBBBBB !important;
    font-weight: 900 !important;
    font-size: 1.8rem !important;
}

[data-testid="stMetricDelta"],
[data-testid="metric-container"] [data-testid="stMetricDelta"] {
    color: #333333 !important;
    font-weight: 600 !important;
}

/* Dark tema için override */
[data-theme="dark"] .summary-heading,
[data-theme="dark"] h4.summary-heading {
    color: #ffffff !important;
}

[data-theme="dark"] [data-testid="metric-container"] {
    background: #1f2937 !important;
    border: 2px solid #374151 !important;
}

[data-theme="dark"] [data-testid="metric-container"] *,
[data-theme="dark"] [data-testid="stMetricLabel"],
[data-theme="dark"] [data-testid="stMetricValue"],
[data-theme="dark"] [data-testid="stMetricDelta"] {
    color: #ffffff !important;
}
</style>
""", unsafe_allow_html=True)

# JavaScript ile DOM manipülasyonu - CSS yetmezse
st.markdown(f"""
<script>
// Current theme from Python
const currentTheme = '{st.session_state["theme"]}';

// Force metric colors with JavaScript
function applyMetricStyles() {{
    const isDark = currentTheme === 'dark' || 
                   document.body.getAttribute('data-theme') === 'dark' ||
                   document.documentElement.getAttribute('data-theme') === 'dark';
    
    const textColor = isDark ? '#000000' : '#ffffff' ;
    const bgColor = isDark ? '#1f2937' : '#ffffff';
    const borderColor = isDark ? '#374151' : '#cccccc';
    const deltaColor = isDark ? '#cbd5e1' : '#333333';
    
    console.log('Theme:', isDark ? 'dark' : 'light', 'TextColor:', textColor);
    
    // İstatistikler başlığı
    const summaryHeadings = document.querySelectorAll('.summary-heading, h4');
    summaryHeadings.forEach(el => {{
        el.style.setProperty('color', textColor, 'important');
        el.style.setProperty('font-weight', '700', 'important');
        el.style.setProperty('opacity', '1', 'important');
    }});
    
    // Metric container'lar
    const metrics = document.querySelectorAll('[data-testid="metric-container"]');
    metrics.forEach(container => {{
        container.style.setProperty('background-color', bgColor, 'important');
        container.style.setProperty('border', `2px solid ${{borderColor}}`, 'important');
        
        // İçindeki tüm elementler
        const allElements = container.querySelectorAll('*');
        allElements.forEach(el => {{
            el.style.setProperty('color', textColor, 'important');
            el.style.setProperty('opacity', '1', 'important');
        }});
        
        // Metric label'lar
        const labels = container.querySelectorAll('[data-testid="stMetricLabel"]');
        labels.forEach(label => {{
            label.style.setProperty('color', textColor, 'important');
            label.style.setProperty('font-weight', '700', 'important');
        }});
        
        // Metric value'lar
        const values = container.querySelectorAll('[data-testid="stMetricValue"]');
        values.forEach(value => {{
            value.style.setProperty('color', textColor, 'important');
            value.style.setProperty('font-weight', '900', 'important');
            value.style.setProperty('font-size', '1.8rem', 'important');
        }});
        
        // Metric delta'lar
        const deltas = container.querySelectorAll('[data-testid="stMetricDelta"]');
        deltas.forEach(delta => {{
            delta.style.setProperty('color', deltaColor, 'important');
            delta.style.setProperty('font-weight', '600', 'important');
        }});
    }});
}}

// İlk yükleme
setTimeout(applyMetricStyles, 500);
setTimeout(applyMetricStyles, 1500);
setTimeout(applyMetricStyles, 3000);

// Sürekli kontrol
setInterval(applyMetricStyles, 2000);

// MutationObserver ile DOM değişikliklerini izle
const observer = new MutationObserver(function(mutations) {{
    let shouldReapply = false;
    mutations.forEach(function(mutation) {{
        if (mutation.type === 'childList' || 
            (mutation.type === 'attributes' && 
             (mutation.attributeName === 'data-theme' || 
              mutation.attributeName === 'class'))) {{
            shouldReapply = true;
        }}
    }});
    if (shouldReapply) {{
        setTimeout(applyMetricStyles, 100);
    }}
}});

observer.observe(document.body, {{
    attributes: true,
    childList: true,
    subtree: true,
    attributeFilter: ['data-theme', 'class']
}});
</script>
""", unsafe_allow_html=True)
