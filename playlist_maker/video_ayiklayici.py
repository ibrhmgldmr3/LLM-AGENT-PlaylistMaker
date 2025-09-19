# -*- coding: utf-8 -*-
import yt_dlp
import time

def youtubede_ara(topic, output_file="playlist_maker/datas/video_linkleri.txt", num_results=2):
    """
    Belirtilen konu için YouTube'da arama yapar ve ilk birkaç video linkini dosyaya kaydeder.
    
    Args:
        topic (str): Arama konusu
        output_file (str): Linklerin kaydedileceği dosya
        num_results (int): Kaydedilecek video sayısı
    """
    print(f"'{topic}' için YouTube'da arama yapılıyor...")
    
    # YouTube arama URL'sini oluştur
    search_url = f"ytsearch{num_results}:{topic}"
    
    # yt-dlp seçeneklerini ayarla - daha güvenli
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': True,
        'skip_download': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'extractor_args': {
            'youtube': {
                'skip': ['dash', 'hls']
            }
        }
    }
    
    try:
        # Rate limiting - arama öncesi bekle
        print(f"⏱️ YouTube arama öncesi 3 saniye bekleniyor...")
        time.sleep(3)
        
        # yt-dlp kullanarak ara ve bilgileri çıkar
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
            
            if 'entries' in info:
                # Çıktı dosyasını aç
                with open(output_file, 'w', encoding='utf-8') as f:
                    # Arama sonuçlarını yinele
                    count = 0
                    for entry in info['entries']:
                        if count >= num_results:
                            break
                            
                        if 'id' in entry and 'title' in entry:
                            video_url = f"https://www.youtube.com/watch?v={entry['id']}"                            
                            # URL'yi dosyaya yaz
                            f.write(f"{video_url}\n")
                            count += 1
                
                print(f"Başarıyla {count} video linki '{output_file}' dosyasına kaydedildi.")
            else:
                print("Arama sonucu bulunamadı.")
                
    except Exception as e:
        print(f"Bir hata oluştu: {e}")
        return False
    
    return True

