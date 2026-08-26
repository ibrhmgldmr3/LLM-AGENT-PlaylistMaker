# Iki asamali: arayuz Node'da derlenir, calisma zamani yalnizca Python tasir.
#
# Neden tek imaj: `api/main._mount_frontend` derlenmis `web/dist`i ayni
# surecten servis ediyor, yani uretimde tek surec hem API'yi hem arayuzu
# karsiliyor. Ayri bir web sunucusu eklemek, bugun olmayan bir sorunu
# cozmek olurdu.

# --------------------------------------------------------------- arayuz
FROM node:22-slim AS web

WORKDIR /web

# Once yalnizca manifest: kaynak her degistiginde `npm ci` yeniden calismasin.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# `gen:types` CALISTIRILMIYOR: o adim Python'dan OpenAPI uretiyor ve bu
# asamada Python yok. Uretilen `src/api/schema.d.ts` depoda tutuluyor,
# tazeligini CI'daki `frontend` isi dogruluyor.
RUN npm run build


# ------------------------------------------------------------ calisma zamani
FROM python:3.13-slim AS runtime

# ffmpeg ASR yolu icin ZORUNLU: `ytdlp_provider.download_audio` 16 kHz mono
# WAV uretiyor ve donusumu ffmpeg yapiyor. Yoksa ASR "ffmpeg WAV donusumu
# yapilamadi" ile basarisiz olur (ENABLE_ASR_FALLBACK=false iken kullanilmaz
# ama acildiginda imajin yeniden kurulmasi gerekmemeli).
RUN apt-get update \
 && apt-get install --no-install-recommends -y ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src/ ./src/
COPY api/ ./api/
COPY scripts/ ./scripts/
COPY --from=web /web/dist ./web/dist

# Kok olmayan kullanici. `data/` ONCEDEN olusturulup sahipligi veriliyor:
# `AppConfig.ensure_directories` acilista yazmaya calisiyor ve baglanan bir
# birimin sahibi kok olursa orada patlar.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/data \
 && chown -R app:app /app
USER app

# Veritabani, calistirma ciktilari ve onbellek burada. Kalici olmasi icin
# baglanmali: `docker run -v playlist-data:/app/data`
VOLUME ["/app/data"]

EXPOSE 8000

# `/api/health` sabit bir govde donuyor; amaci surecin istek karsilayabildigini
# soylemek. Yapilandirma hatalari ilk GERCEK istekte yuzeye cikiyor, bu yuzden
# saglik ucu onlari maskelemiyor.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# TEK SUREC -- `--workers` EKLEMEYIN.
#
# Is durumu (`InProcessJobRunner`) bellekte tutuluyor: tutamaclar, SSE olay
# kanallari ve future'lar surece ait. Ikinci bir surece dusen istek
# calistirmayi TANIMAZ; ilerleme akisi kopar ve `/status` "bilinmeyen
# calistirma" doner -- is aslinda saglikli calisiyorken. Ayni sebeple bu imaj
# birden fazla replika ile olceklenemez.
#
# Uygulama `WEB_CONCURRENCY > 1` gorurse acilista bunu hata seviyesinde
# bildiriyor (bkz. `api/main._warn_on_multi_process`).
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
