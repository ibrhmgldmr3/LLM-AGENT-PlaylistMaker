# -*- coding: utf-8 -*-

import os
import json
import re
import traceback
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage
from playlist_maker.ses_transkriptleri import cikti_klasorunu_temizle, video_linklerini_isle

# Ortam değişkenlerini yükle
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_API_URL")

llm = ChatOpenAI(
    model="gpt-3.5-turbo",
    openai_api_key=api_key,
    base_url=base_url,
    temperature=0.3
)

def klasorden_transkriptleri_yukle(directory):
    transcripts = []
    try:
        directory_path = Path(directory)
        if not directory_path.exists():
            print(f"Hata: {directory} dizini bulunamadı!")
            return transcripts

        for transcript_file in directory_path.glob("*_transcript.json"):
            try:
                with open(transcript_file, 'r', encoding='utf-8') as f:
                    transcript_data = json.load(f)

                file_name = transcript_file.stem
                match = re.search(r'_v_([a-zA-Z0-9_-]{11})', file_name)
                video_id = match.group(1) if match else None
                if not video_id:
                    print(f"Video ID çıkarılamadı: {transcript_file}")
                    continue

                transcript_text = transcript_data.get("text", str(transcript_data))
                transcripts.append({
                    "video_id": video_id,
                    "transcript": transcript_text,
                    "file_path": str(transcript_file)
                })
            except Exception as e:
                print(f"Hata: {transcript_file} okunurken sorun oluştu: {str(e)}")

        print(f"{len(transcripts)} transkript dosyası başarıyla yüklendi.")
    except Exception as e:
        print(f"Dizin okunurken hata: {str(e)}")
    return transcripts

def langchainle_analiz_et(transcript_text, topic):
    try:
        prompt = f"""
Aşağıda bir YouTube videosunun transkripti verilmiştir. Lütfen bu transkripti analiz ederek, videonun belirtilen konuya ne kadar uygun olduğunu çok boyutlu olarak değerlendir.

---

## 🎯 Konu:
"{topic}"

## 🎧 Video Transkripti:
{transcript_text[:2000]}...  # Transkripti kısaltıyoruz

---

Yanıtı sadece aşağıdaki JSON formatında ver:
{{
  "kapsam_uyumu": int,
  "bilgi_derinligi": int,
  "anlatim_tarzi": int,
  "hedef_kitle": int,
  "yapisal_tutarlilik": int,
  "genel_puan": int,
  "yorum": "kısa bir genel yorum"
}}
"""
        response = llm.invoke([HumanMessage(content=prompt)])
        if not response or not getattr(response, "content", None):
            print("⚠️ API yanıtı boş veya None döndü.")
            return None
        return response.content.strip()

    except Exception as e:
        print(f"API çağrısı sırasında hata: {str(e)}")
        traceback.print_exc()
        return None

def json_verilerini_parsla(response_text):
    try:
        json_match = re.search(r'\{[^{}]*\}', response_text)
        if json_match:
            data = json.loads(json_match.group(0))
            return data.get("genel_puan", 0), data
    except Exception:
        pass
    return 0, {
        "kapsam_uyumu": 0,
        "bilgi_derinligi": 0,
        "anlatim_tarzi": 0,
        "hedef_kitle": 0,
        "yapisal_tutarlilik": 0,
        "genel_puan": 0,
        "yorum": "Değerlendirme yapılamadı."
    }

def tum_transkriptleri_analiz_et(transcripts, topic):
    best_score = -1
    best_video_id = ""
    tum_sonuclar = []

    if not transcripts:
        print("Uyarı: Analiz edilecek transkript bulunamadı!")
        return best_video_id

    for item in transcripts:
        try:
            print(f"Analiz ediliyor: {item['video_id']}")
            result_text = langchainle_analiz_et(item["transcript"], topic)
            if not result_text or not isinstance(result_text, str):
                print(f"⚠️ {item['video_id']} için analiz sonucu alınamadı.")
                continue

            score, parsed_data = json_verilerini_parsla(result_text.strip())
            if score > best_score:
                best_score = score
                best_video_id = item["video_id"]
            tum_sonuclar.append({"video_id": item["video_id"], "score": score, **parsed_data})
            print(f"  🎯 Skor: {score}")
        except Exception as e:
            print(f"{item['video_id']} analiz edilirken hata: {str(e)}")
            traceback.print_exc()
            continue

    if tum_sonuclar:
        analiz_sonuclarini_kaydet(tum_sonuclar)
    return best_video_id

def save_best_video(video_id, transcripts, output_file="en_iyi_video.txt"):
    if not video_id:
        print("Uyarı: Eklenecek video ID'si bulunamadı!")
        return

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with open(output_file, "a", encoding="utf-8") as file:
            file.write(f"{youtube_url}\n")
        print(f"Video URL'si dosyaya eklendi: {youtube_url}")
    except Exception as e:
        print(f"URL dosyaya yazılırken hata: {str(e)}")

def analiz_sonuclarini_kaydet(results, output_file="analiz_sonuclari.json"):
    try:
        with open(output_file, "w", encoding="utf-8") as file:
            json.dump(results, file, ensure_ascii=False, indent=2)
        print(f"Analiz sonuçları '{output_file}' dosyasına kaydedildi.")
    except Exception as e:
        print(f"Analiz sonuçları kaydedilirken hata: {str(e)}")

def tum_islemler(konu_basligi):
    try:
        video_links_path = "playlist_maker/datas/video_linkleri.txt"
        output_dir = "playlist_maker/datas/ses_transkriptleri"

        if not os.path.exists(video_links_path):
            print("Hata: video_linkleri.txt dosyası bulunamadı!")
            return

        cikti_klasorunu_temizle(output_dir)
        video_linklerini_isle(video_links_path, output_dir)
        transcripts = klasorden_transkriptleri_yukle(output_dir)
        if not transcripts:
            print("Hata: Analiz edilecek transkript bulunamadı!")
            return

        best_video_id = tum_transkriptleri_analiz_et(transcripts, konu_basligi)
        if not best_video_id:
            print("\n⚠️ En iyi video bulunamadı!")
            return

        print(f"\n🎯 En yüksek puanlı video: https://www.youtube.com/watch?v={best_video_id}")
        save_best_video(best_video_id, transcripts)

    except Exception as e:
        print(f"Ana işlem sırasında hata oluştu: {str(e)}")
        traceback.print_exc()
