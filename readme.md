# Make A Playlist

Bu proje, kullanıcıların belirli bir konu hakkında öğrenme çalma listeleri oluşturmasına olanak tanıyan bir eğitim aracıdır.

## Proje Açıklaması

Make A Playlist, öğrenme sürecini kolaylaştırmak için geliştirilmiş bir eğitim içeriği organizasyon aracıdır. Uygulama, herhangi bir konu için en önemli 10 alt başlığı belirler ve her alt başlık için en uygun eğitim videolarını seçerek kapsamlı bir öğrenme çalma listesi oluşturur.

## Gereksinimler

- Python 3.7 veya üstü
- pip (Python paket yöneticisi)
- İnternet bağlantısı
- OpenRouter API anahtarı (ücretsiz hesap oluşturabilirsiniz)

## Kurulum

1. Repoyu klonlayın:
    ```
    git clone https://github.com/ibrhmgldmr3/LLM-AGENT-PlaylistMaker.git
    ```

2. Proje dizinine gidin:
    ```
    cd make_a_playlist
    ```

3. Gerekli paketleri yükleyin:
    ```
    pip install -r requirements.txt
    ```

4. **API Anahtarı Kurulumu**:
   
   a. [OpenRouter](https://openrouter.ai/) sitesinden ücretsiz hesap oluşturun
   
   b. API anahtarınızı alın
   
   c. Proje dizininde `.env` dosyasını oluşturun ve aşağıdaki içeriği ekleyin:
   ```
   OPENAI_API_KEY=your_openrouter_api_key_here
   OPENAI_API_URL=https://openrouter.ai/api/v1
   ```
   
   d. `your_openrouter_api_key_here` yerine gerçek API anahtarınızı yazın

## Kullanım

1. Uygulamayı başlatın:
    ```
    python -m streamlit run make_a_playlist.py
    ```

2. Web tarayıcınızda http://localhost:8501 adresine gidin

3. Öğrenmek istediğiniz ana konuyu girin (örn. "Yapay Zeka", "Web Geliştirme").

4. Uygulama konuyla ilgili en önemli alt başlıkları analiz edecektir (maksimum 10 adet).

5. Her alt başlık için en uygun eğitim videoları otomatik olarak seçilir.

6. Oluşturulan öğrenme çalma listesini görüntüleyebilirsiniz.

## Kullanılan Teknolojiler

- **Streamlit**: Web arayüzü
- **LangChain**: AI model entegrasyonu
- **OpenRouter**: AI model API'si
- **yt-dlp**: YouTube video işleme
- **faster-whisper**: Ses tanıma ve transkripsiyon

## Sorun Giderme

**404 Hatası**: Eğer "Not Found" hatası alıyorsanız:
- API anahtarınızın doğru olduğundan emin olun
- `.env` dosyasının doğru yapılandırıldığından emin olun
- İnternet bağlantınızı kontrol edin

**Paket Hatası**: Eğer modül bulunamadı hatası alıyorsanız:
```bash
pip install -r requirements.txt
```

