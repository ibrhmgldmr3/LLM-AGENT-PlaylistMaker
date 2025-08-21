# -*- coding: utf-8 -*-

import yt_dlp
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
    
    # yt-dlp seçeneklerini yapılandır
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'force_generic_extractor': True,
        'skip_download': True,
    }
    
    try:
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

