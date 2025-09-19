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
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
print("✅ Whisper modeli yüklendi.")

def transkriptle(url, cikti_dosyasi="playlist_maker/datas/ses_transkriptleri"):
    """YouTube videosu için HIZLI SIRA ile transkript alır:
    1. YouTube Transcript API (en hızlı)
    2. yt-dlp otomatik altyazılar 
    3. faster-whisper (en yavaş ama en güvenilir)
    """
    try:
        # Video ID üret
        video_id = url.replace("https://", "").replace("http://", "").replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_")
        cikti_yolu = Path(cikti_dosyasi)
        cikti_yolu.mkdir(parents=True, exist_ok=True)

        transkript_dosyasi = cikti_yolu / f"{video_id}_transcript.json"
        
        # ADIM 1: YouTube Transcript API'sini dene (en hızlı)
        print("🚀 1. YouTube Transcript API deneniyor...")
        video_id_clean = url.split("v=")[1].split("&")[0] if "v=" in url else url.split("/")[-1]
        
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            
            # API instance oluştur
            api = YouTubeTranscriptApi()
            
            # Dil öncelikleri: Türkçe ve İngilizce
            languages_to_try = ['tr', 'en']
            
            for lang in languages_to_try:
                try:
                    # Doğru API kullanımı - fetch() methodu ile
                    transcript_data = api.fetch(video_id_clean, languages=[lang])
                    
                    # FetchedTranscript üzerinde iterate
                    transcript_text = ' '.join([snippet.text for snippet in transcript_data])
                    
                    if len(transcript_text.strip()) > 100:
                        result = {
                            "video_id": video_id,
                            "url": url,
                            "detected_language": lang,
                            "text": transcript_text
                        }
                        with open(transkript_dosyasi, "w", encoding="utf-8") as f:
                            json.dump(result, f, ensure_ascii=False, indent=2)
                        lang_name = "Türkçe" if lang == 'tr' else "İngilizce"
                        print(f"✅ YouTube Transcript API'den {lang_name} transkript alındı!")
                        return result
                        
                except Exception as e:
                    if "IpBlocked" in str(e) or "blocked" in str(e).lower():
                        lang_name = "Türkçe" if lang == 'tr' else "İngilizce"
                        print(f"⚠️ YouTube Transcript API IP bloğu ({lang_name}): {e}")
                        break  # IP blok varsa diğer dilleri denemeye gerek yok
                    else:
                        lang_name = "Türkçe" if lang == 'tr' else "İngilizce"
                        print(f"⚠️ {lang_name} transkript bulunamadı: {str(e)[:100]}...")
                        continue  # Bir sonraki dili dene
                
        except Exception as e:
            print(f"⚠️ YouTube Transcript API başarısız: {e}")

        print("🚀 2. yt-dlp otomatik altyazıları deneniyor...")
        
        # YouTube video bilgilerini çek - daha güvenli header'lar ile
        ydl_opts = {
            "quiet": True, 
            "skip_download": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "extractor_args": {
                "youtube": {
                    "skip": ["dash", "hls"]
                }
            }
        }
        
        # Rate limiting - istekler arası bekle
        print(f"⏱️ İstek öncesi 2 saniye bekleniyor...")
        time.sleep(2)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Otomatik altyazı varsa oradan çek
        if "automatic_captions" in info:
            captions = info["automatic_captions"]
            # Önce Türkçe, sonra İngilizce dene, yoksa ilk mevcut dili al
            lang = None
            vtt_url = None  # Değişkeni başlangıçta tanımla
            
            if "tr" in captions:
                lang = "tr"
            elif "en" in captions:
                lang = "en"
            else:
                lang = list(captions.keys())[0] if captions else None
            
            if lang:
                formats = captions[lang]
                vtt_url = next((f["url"] for f in formats if f["ext"] == "vtt"), None)

            if vtt_url:
                print(f"🎯 YouTube otomatik altyazısı bulundu ({lang}) — indiriliyor...")
                vtt_text = requests.get(vtt_url).text
                
                # Google hata sayfası kontrolü
                if ("We're sorry" in vtt_text or "automated queries" in vtt_text or 
                    "<html>" in vtt_text or "Sorry..." in vtt_text or "<!DOCTYPE" in vtt_text):
                    print("🚫 Google hata sayfası tespit edildi, Whisper'a geçiliyor...")
                    print(f"📄 İlk 500 karakter: {vtt_text[:500]}")
                else:
                    # VTT'den düz metne çevir (basit)
                    clean_lines = []
                    for line in vtt_text.splitlines():
                        if "-->" in line or line.strip() == "" or line.strip().isdigit():
                            continue
                        clean_lines.append(line.strip())
                    full_text = " ".join(clean_lines)

                    # Metin çok kısa mı kontrol et
                    if len(full_text.strip()) > 100:  # En az 100 karakter
                        transcript_data = {
                            "video_id": video_id,
                            "url": url,
                            "detected_language": lang,
                            "text": full_text
                        }

                        with open(transkript_dosyasi, "w", encoding="utf-8") as f:
                            json.dump(transcript_data, f, ensure_ascii=False, indent=2)
                        print(f"✅ yt-dlp'den altyazı transkripti alındı!")
                        return transcript_data
                    else:
                        print("⚠️ Altyazı çok kısa, Whisper ile denenecek...")
        else:
            print("⚠️ yt-dlp'de otomatik altyazı bulunamadı")

        # ADIM 3: faster-whisper ile ses dosyasını işle (en yavaş ama en güvenilir)
        print("🚀 3. Whisper ile ses transkripti oluşturuluyor...")
        return whisper_transkript_olustur(url, video_id, transkript_dosyasi, info)

    except Exception as e:
        print(f"❗ transkriptle() içinde hata oluştu: {e}")
        traceback.print_exc()
        return None

def whisper_transkript_olustur(url, video_id, transkript_dosyasi, info):
    """Whisper ile ses dosyasından transkript oluşturur"""
    try:

        # Ses dosyasını indir
        timestamp = int(time.time())
        ses_dosyasi_temsil = os.path.abspath(f"temp_{video_id}_{timestamp}")
        ses_dosyasi_mp3 = ses_dosyasi_temsil + ".mp3"

        # FFmpeg yolunu belirle
        ffmpeg_path = os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0-essentials_build\bin")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': ses_dosyasi_temsil,
            'ffmpeg_location': ffmpeg_path,  # FFmpeg yolunu belirt
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
        segments, whisper_info = whisper_model.transcribe(ses_dosyasi_mp3, language=None)
        if not segments:
            print("⚠️ Ses dosyası analiz edilemedi, segment bulunamadı.")
            return None

        full_text = " ".join([seg.text for seg in segments])
        transcript_data = {
            "video_id": video_id,
            "url": url,
            "detected_language": whisper_info.language,
            "text": full_text.strip()
        }

        with open(transkript_dosyasi, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Whisper transkripti kaydedildi: {transkript_dosyasi}")

        # Temp dosyayı sil
        if os.path.exists(ses_dosyasi_mp3):
            os.remove(ses_dosyasi_mp3)
            print(f"🗑️ Silindi: {ses_dosyasi_mp3}")

        return transcript_data

    except Exception as e:
        print(f"❗ Whisper transkript oluşturma hatası: {str(e)}")
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