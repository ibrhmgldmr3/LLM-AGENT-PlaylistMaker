# Make A Playlist

Bu proje, kullanıcıların belirli bir konu hakkında öğrenme çalma listeleri oluşturmasına olanak tanıyan bir eğitim aracıdır.

## Proje Açıklaması

Make A Playlist, öğrenme sürecini kolaylaştırmak için geliştirilmiş bir eğitim içeriği organizasyon aracıdır. Uygulama, herhangi bir konu için en önemli 10 alt başlığı belirler ve her alt başlık için en uygun eğitim videolarını seçerek kapsamlı bir öğrenme çalma listesi oluşturur.

## Gereksinimler

- Python 3.7 veya üstü
- pip (Python paket yöneticisi)
- İnternet bağlantısı

## Kurulum

1. Repoyu klonlayın:
    ```
    git clone https://github.com/kullaniciadi/make_a_playlist.git
    ```

2. Proje dizinine gidin:
    ```
    cd make_a_playlist
    ```

3. Gerekli paketleri yükleyin:
    ```
    pip install -r requirements.txt
    ```

## Kullanım

1. Uygulamayı başlatın:
    ```
    python -m streamlit run .\make_a_playlist.py
    ```

2. Öğrenmek istediğiniz ana konuyu girin (örn. "Yapay Zeka", "Web Geliştirme").

3. Uygulama konuyla ilgili en önemli alt başlıkları analiz edecektir (maksimum 10 adet).

4. Her alt başlık için en uygun eğitim videoları otomatik olarak seçilir.

5. Oluşturulan öğrenme çalma listesini JSON veya CSV formatında dışa aktarabilirsiniz.

