# -*- coding: utf-8 -*-

import streamlit as st
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from playlist_maker.transkript_analizi import tum_transkriptleri_analiz_et, tum_islemler
from playlist_maker.video_ayiklayici import youtubede_ara
import yt_dlp

os.environ["STREAMLIT_DISABLE_WATCHDOG_WARNINGS"] = "true"
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_API_URL")

st.title("📘 Konu Öğrenme Asistanı")
st.subheader("Bir konu girin, alt başlıklarını öğrenin ve size uygun bir playlist yapalım.")

llm = ChatOpenAI(
    model="gpt-3.5-turbo",
    openai_api_key=api_key,
    base_url=base_url,
    temperature=0.3,
    max_tokens=1500,
)

def altbasliklari_cikar(konu):
    try:
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

def alt_basliklar_icin_videolar(altbasliklar):
    try:
        cikti_dosyasi = "en_iyi_video.txt"
        video_linkleri_yolu = Path(cikti_dosyasi)
        video_linkleri_yolu.parent.mkdir(parents=True, exist_ok=True)

        with open(video_linkleri_yolu, "w", encoding="utf-8") as f:
            f.write("")

        st.info("Video linkleri dosyası temizlendi.")

        for altbaslik in altbasliklar:
            cikti_dosyasi = "playlist_maker/datas/video_linkleri.txt"
            search_success = youtubede_ara(altbaslik, output_file=cikti_dosyasi, num_results=2)
            if not search_success:
                st.warning(f"{altbaslik} için video araması başarısız oldu.")
                continue

            video_linkleri_yolu = Path(cikti_dosyasi)
            if not video_linkleri_yolu.exists() or video_linkleri_yolu.stat().st_size == 0:
                st.warning(f"{altbaslik} için video bulunamadı.")
                continue

            with open(video_linkleri_yolu, 'r', encoding='utf-8') as f:
                video_links = [line.strip() for line in f if line.strip()]

            for link in video_links:
                try:
                    ydl_opts = {"quiet": True, "skip_download": True}
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(link, download=False)

                    # Eğer YouTube'da otomatik altyazı varsa
                    if info.get("automatic_captions"):
                        st.info(f"{link} için YouTube otomatik altyazısı bulundu. Orijinal transkript kullanılacak.")
                        transcript_data = {
                            "video_id": info.get("id"),
                            "url": link,
                            "detected_language": list(info["automatic_captions"].keys())[0],
                            "text": "[YouTube altyazı verisi bu sürümde işlenmiyor]"
                        }
                        output_path = Path("playlist_maker/datas/ses_transkriptleri")
                        output_path.mkdir(parents=True, exist_ok=True)
                        transcript_file = output_path / f"youtube_{info.get('id')}_transcript.json"
                        with open(transcript_file, "w", encoding="utf-8") as tf:
                            json.dump(transcript_data, tf, ensure_ascii=False, indent=2)
                        continue

                except Exception as e:
                    st.warning(f"{link} için altyazı kontrolü sırasında hata: {e}")

            tum_islemler(altbaslik)

        st.session_state["videolar_yuklendi"] = True
        st.success("Tüm alt başlıklar işlendi.")
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

konu = st.text_input("🎯 Öğrenmek istediğiniz konuyu giriniz:")

if konu and st.button("📋 İçerikleri Getir"):
    with st.spinner("Alt başlıklar oluşturuluyor..."):
        altbasliklar = altbasliklari_cikar(konu)
        if altbasliklar:
            st.session_state["konu"] = konu
            st.session_state["altbasliklar"] = altbasliklar
            st.rerun()
        else:
            st.warning("Alt başlıklar oluşturulurken bir hata çıktı. Lütfen farklı bir konu deneyiniz!")

if "altbasliklar" in st.session_state and "konu" in st.session_state:
    st.subheader(f"📌 {st.session_state.konu} Konusuna Ait Alt Başlıklar")
    for i, altbaslik in enumerate(st.session_state.altbasliklar, 1):
        st.write(f"{i}. {altbaslik}")

    if st.button("🎥 Alt başlıklara uygun videoları ara"):
        with st.spinner("Videolar aranıyor ve analiz ediliyor..."):
            alt_basliklar_icin_videolar(st.session_state.altbasliklar)

if st.session_state.get("videolar_yuklendi"):
    best_videos = en_iyi_videolari_yukle()
    if best_videos:
        st.success("Videolar bulundu! Önerilen kaynaklar:")
        for i, video_url in enumerate(best_videos, 1):
            st.write(f"Video {i}:")
            st.video(video_url)
    else:
        st.warning("Hiçbir video bulunamadı.")

st.sidebar.title("ℹ️ İçerik Hakkında")
st.sidebar.info(
    "Bu uygulama, bir konuya yönelik öğrenilmesi gereken alt başlıkları belirler ve her biri için "
    "YouTube videoları önerir. Geliştirici: [ibrhmgldmr](https://github.com/ibrhmgldmr3)"
)
