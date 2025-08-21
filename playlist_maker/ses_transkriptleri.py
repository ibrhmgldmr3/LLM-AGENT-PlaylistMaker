# -*- coding: utf-8 -*-
import traceback
import re
import requests
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import os
import time
import json
import yt_dlp
from faster_whisper import WhisperModel

# Globalde model sadece bir kez yüklenir
print("🔄 Whisper modeli yükleniyor...")
whisper_model = WhisperModel("base", compute_type="int8")
print("✅ Whisper modeli yüklendi.")

def transkriptle(url, cikti_dosyasi="playlist_maker/datas/ses_transkriptleri"):
    """YouTube videosu için varsa orijinal altyazıyı alır, yoksa faster-whisper ile transkript üretir."""
    try:
        # Video ID üret
        video_id = url.replace("https://", "").replace("http://", "").replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_")
        cikti_yolu = Path(cikti_dosyasi)
        cikti_yolu.mkdir(parents=True, exist_ok=True)

        transkript_dosyasi = cikti_yolu / f"{video_id}_transcript.json"

        # YouTube video bilgilerini çek
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Otomatik altyazı varsa oradan çek
        if "automatic_captions" in info:
            captions = info["automatic_captions"]
            lang = "tr" if "tr" in captions else list(captions.keys())[0]
            formats = captions[lang]
            vtt_url = next((f["url"] for f in formats if f["ext"] == "vtt"), None)

            if vtt_url:
                print(f"🎯 YouTube otomatik altyazısı bulundu ({lang}) — indiriliyor...")
                vtt_text = requests.get(vtt_url).text
                # VTT'den düz metne çevir (basit)
                clean_lines = []
                for line in vtt_text.splitlines():
                    if "-->" in line or line.strip() == "" or line.strip().isdigit():
                        continue
                    clean_lines.append(line.strip())
                full_text = " ".join(clean_lines)

                transcript_data = {
                    "video_id": video_id,
                    "url": url,
                    "detected_language": lang,
                    "text": full_text
                }

                with open(transkript_dosyasi, "w", encoding="utf-8") as f:
                    json.dump(transcript_data, f, ensure_ascii=False, indent=2)
                print(f"✅ YouTube altyazısından transkript kaydedildi: {transkript_dosyasi}")
                return transcript_data

        # Eğer buraya geldiyse orijinal altyazı yok, faster-whisper kullan
        print("⚠️ YouTube altyazısı yok, faster-whisper ile transkript oluşturuluyor...")

        # Ses dosyasını indir
        timestamp = int(time.time())
        ses_dosyasi_temsil = os.path.abspath(f"temp_{video_id}_{timestamp}")
        ses_dosyasi_mp3 = ses_dosyasi_temsil + ".mp3"

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': ses_dosyasi_temsil,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            print(f"🎬 Video indirildi: {info.get('title', 'Bilinmeyen başlık')}")

        if not os.path.exists(ses_dosyasi_mp3):
            print("❌ Ses dosyası indirilemedi.")
            return None

        # Whisper ile transkript üret
        segments, info = whisper_model.transcribe(ses_dosyasi_mp3, language=None)
        if not segments:
            print("⚠️ Ses dosyası analiz edilemedi, segment bulunamadı.")
            return None

        full_text = " ".join([seg.text for seg in segments])
        transcript_data = {
            "video_id": video_id,
            "url": url,
            "detected_language": info.language,
            "text": full_text.strip()
        }

        with open(transkript_dosyasi, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Whisper transkripti kaydedildi: {transkript_dosyasi}")

        if os.path.exists(ses_dosyasi_mp3):
            os.remove(ses_dosyasi_mp3)
            print(f"🗑️ Silindi: {ses_dosyasi_mp3}")

        return transcript_data

    except Exception as e:
        print(f"❗ transkriptle() içinde hata oluştu: {str(e)}")
        traceback.print_exc()
        return None


def cikti_klasorunu_temizle(cikti_dosyasi):
    """Çıktı klasörünü temizler"""
    if Path(cikti_dosyasi).exists():
        print(f"{cikti_dosyasi} klasörü temizleniyor...")
        for file in Path(cikti_dosyasi).glob("*"):
            try:
                file.unlink()  # Dosyayı sil
                print(f"Silindi: {file}")
            except Exception as e:
                print(f"Silinemedi: {file} - Hata: {str(e)}")
        print("Klasör temizleme tamamlandı.")
    
    # Çıktı dizinini oluştur (yoksa)
    os.makedirs(cikti_dosyasi, exist_ok=True)

def video_linklerini_isle(video_links_file, cikti_dosyasi="playlist_maker/datas/ses_transkriptleri/"):
    """Bir dosyadaki tüm video linklerini işler ve transkriptlerini oluşturur"""
    # Video linklerini oku
    with open(video_links_file, 'r', encoding='utf-8') as file:
        video_links = [line.strip() for line in file if line.strip()]
    
    results = []
    # Her link için işlem yap
    for i, link in enumerate(video_links, 1):
        print(f"İşleniyor: {i}/{len(video_links)} - {link}")
        
        try:
            transcript_data = transkriptle(link, cikti_dosyasi)
            if transcript_data:
                results.append(transcript_data)
        except Exception as e:
            print(f"Hata: {link} işlenirken bir sorun oluştu: {str(e)}")
    
    print("Tüm linkler işlendi!")
    return results

def transkript_cikar():
    video_linkleri_yolu = Path("playlist_maker/datas/video_linkleri.txt")
    cikti_dosyasi = Path("playlist_maker/datas/ses_transkriptleri/")
    
    # Klasörü temizle ve video linklerini işle
    cikti_klasorunu_temizle(cikti_dosyasi)
    video_linklerini_isle(video_linkleri_yolu, cikti_dosyasi)
    
# Bu dosya doğrudan çalıştırıldığında
if __name__ == "__main__":
    # Dosya yollarını tanımla
    video_linkleri_yolu = Path("playlist_maker/datas/video_linkleri.txt")
    cikti_dosyasi = Path("playlist_maker/datas/ses_transkriptleri/")
    
    # Klasörü temizle ve video linklerini işle
    cikti_klasorunu_temizle(cikti_dosyasi)
    video_linklerini_isle(video_linkleri_yolu, cikti_dosyasi)