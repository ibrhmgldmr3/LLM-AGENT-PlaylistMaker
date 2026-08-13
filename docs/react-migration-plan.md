# Streamlit → React Göç Planı

**Hedef:** Streamlit arayüzünü FastAPI + React (Vite, TypeScript) ile değiştirmek.
**Bağlam:** Şu an yerelde tek kullanıcı geliştiriliyor; ileride herkese açık çok
kullanıcılı yapıya geçilecek.

Planın ana ilkesi: **çok kullanıcılı için TASARLA, çok kullanıcılı altyapıyı ŞİMDİ KURMA.**
Sınırları bugün doğru çizmek neredeyse bedava; sonradan şema ve arayüz değiştirmek pahalı.

---

## 1. Başlangıç durumu

İş mantığı framework'ten bağımsız — bu göçü yeniden yazım değil, arayüz değişimi yapıyor.

```
Taşınacak / silinecek : app.py (163) + src/ui/ (338)   ≈   500 satır
Olduğu gibi kalacak   : services, providers, storage,  ≈ 2.600 satır
                        models, utils, config
```

`src/services`, `src/providers`, `src/storage`, `src/models`, `src/utils` içinde
**tek bir `streamlit` importu yok.** 129 test bu katmanları kapsıyor ve göç boyunca
geçmeye devam etmeli — regresyon ölçütümüz bu.

### Yeniden tasarım gerektiren üç nokta

| # | Sorun | Neden |
|---|---|---|
| 1 | `build_playlist` bloklayıcı, `run_id`'yi kendi içinde üretiyor | HTTP isteği 4–40 sn (ASR ile dakikalar) bekleyemez; API iş kimliğini anında döndürmeli |
| 2 | OAuth sunucuda tarayıcı açıyor (`flow.run_local_server`) | Web uygulamasında kavramsal olarak imkânsız; redirect akışı şart |
| 3 | `run`, `run_subtopic`, `run_video` tabloları yalnızca yazılıyor | Okuma metodu yok; "geçmiş çalıştırmalar" için veri hazır ama erişilemiyor |

---

## 2. Belirleyici karar: BYOK (kendi anahtarını getir)

YouTube Data API kotası **proje başına günde 10.000 birim**. Bir çalıştırmanın maliyeti:

| Çağrı | Adet (6 alt konu, iki dilli) | Birim | Toplam |
|---|---|---|---|
| `search.list` | 12 | 100 | 1.200 |
| `videos.list` | ~12 | 1 | ~12 |
| `channels.list` | ~12 | 1 | ~12 |
| | | | **≈ 1.224** |

Paylaşımlı anahtarla **günde ~8 çalıştırma, tüm kullanıcılar toplamı.** Herkese açık bir
serviste kullanılamaz. Gemini'nin ücretsiz katmanı da benzer şekilde sınırlı.

**Karar: her kullanıcı kendi Gemini ve YouTube anahtarını sağlar.**

Bu, mimarinin geri kalanını belirliyor:

- `AppConfig` tek global nesne olmaktan çıkar → **sunucu ayarı** + **kullanıcı kimlik
  bilgisi** + **çalıştırma seçeneği** olarak üçe ayrılır
- Kimlik bilgileri veritabanında, **şifrelenmiş** saklanır (asla API yanıtında dönmez)
- Kota tükenmesi kullanıcının kendi sorunu olur — adil ve ölçeklenebilir
- Anahtarsız kullanıcı için `yt-dlp` yedeği çalışır ama sunucu IP'sinden hız sınırına
  takılır (bu oturumda birebir yaşandı) — "sınırlı deneme modu" olarak konumlanabilir

### Config bölünmesi (ölçüldü: 45 değişken)

| Grup | Adet | Nereye gider |
|---|---|---|
| **Kullanıcı sırrı** | 8 | Şifreli DB, kullanıcı başına |
| **Çalıştırma seçeneği** | 10 | İstek gövdesi / kullanıcı tercihi |
| **Sunucu ayarı** | 27 | `.env`, tüm sunucu için ortak |

Kullanıcı sırları: `GEMINI_API_KEY`, `YOUTUBE_DATA_API_KEY`,
`YOUTUBE_OAUTH_CLIENT_SECRET(_FILE)`, `YOUTUBE_OAUTH_TOKEN_FILE`, `YTDLP_PROXY`,
`YTDLP_COOKIES_FROM_BROWSER`, `YTDLP_COOKIES_FILE`.

Çalıştırma seçenekleri: `GEMINI_MODEL`, `MAX_SUBTOPICS`,
`SEARCH_CANDIDATES_PER_SUBTOPIC`, `METADATA_TOP_K`, `TRANSCRIPT_ENRICHMENT_TOP_K`,
`ENABLE_ASR_FALLBACK`, `MAX_ASR_VIDEOS_PER_RUN`, `INCLUDE_ENGLISH_BY_DEFAULT`,
`CHANNEL_REPEAT_PENALTY`, `YOUTUBE_PLAYLIST_PRIVACY_STATUS`.

---

## 3. Hedef mimari

```
web/                     Vite + React + TypeScript
  src/
    api/                 OpenAPI'den üretilen tipler + fetch sarmalayıcıları
    features/
      run/               form, ilerleme, sonuç kartları
      history/           geçmiş çalıştırmalar
      settings/          kullanıcı anahtarları, OAuth bağlantısı
    theme/               theme.py paletinden taşınan CSS değişkenleri

api/                     FastAPI (YENİ)
  main.py                uygulama, CORS, hata işleyicileri
  deps.py                get_current_user, get_user_config
  routers/
    runs.py              POST /runs, GET /runs/{id}, SSE
    auth.py              OAuth redirect akışı
    settings.py          kullanıcı anahtarları
    config.py            yetenek keşfi
  jobs.py                JobRunner arayüzü + süreç-içi uygulama

src/                     DEĞİŞMEZ (services, providers, storage, models, utils)
```

### API yüzeyi

```
POST   /api/runs                 → 202 { run_id }        (anında döner)
GET    /api/runs/{id}/events     → SSE: ProgressEvent akışı
GET    /api/runs/{id}            → PlaylistResult
DELETE /api/runs/{id}            → çalışan işi iptal et
GET    /api/runs                 → geçmiş (sayfalı)
GET    /api/runs/{id}/export.md  → study_plan.md
POST   /api/runs/{id}/publish    → YouTube playlist yayınla (build'den AYRI)

GET    /api/config               → yetenekler: hangi anahtar kurulu, ASR hazır mı
PUT    /api/settings/credentials → kullanıcı anahtarlarını kaydet (yalnız yazma)
GET    /api/auth/youtube/start   → Google onay URL'i döndür
GET    /api/auth/youtube/callback→ kodu token'a çevir, kullanıcıya bağla
```

**Neden SSE, WebSocket değil:** akış tek yönlü (sunucu → istemci). Mevcut
`progress_callback(ProgressEvent)` deseni SSE'ye birebir oturuyor. WebSocket'in çift
yönlülüğüne ihtiyaç yok; SSE proxy'lerden daha sorunsuz geçer ve otomatik yeniden bağlanır.

**Neden FastAPI:** `PlaylistResult`, `ProgressEvent`, `FilterOptions` zaten pydantic.
Response şemaları bire bir hazır; OpenAPI'den React tipleri otomatik üretilir. Ayrı bir
serileştirme katmanı yazmaya gerek yok.

### İş yürütme soyutlaması

Bugün süreç-içi, yarın Redis/Celery — ama **servisler bunu hiç bilmemeli**:

```python
class JobRunner(Protocol):
    def submit(self, run_id: str, fn: Callable) -> None: ...
    def subscribe(self, run_id: str) -> Iterator[ProgressEvent]: ...
    def cancel(self, run_id: str) -> bool: ...
```

Faz 1'de `InProcessJobRunner` (ThreadPoolExecutor + `queue.Queue`). Çok kullanıcıya
geçişte `CeleryJobRunner` eklenir, çağıran kod değişmez. FastAPI'nin `BackgroundTasks`'ı
servis katmanına **sızdırılmaz**.

---

## 4. Fazlar

Her faz bağımsız olarak sevk edilebilir ve 129 test geçmeye devam eder.

### Faz 0 — Arka uç hazırlığı ✅ TAMAMLANDI

Davranış değişmedi, yalnızca sınırlar netleşti. Streamlit çalışmaya devam ediyor.

- [x] `build_playlist(..., run_id=None, user_id="local")` — kimlik dışarıdan verilebilir
- [x] `SQLiteStore`: `get_run`, `get_run_summary`, `list_runs`, `count_runs`, `delete_run`
- [x] `run` tablosuna `user_id` kolonu + `idx_run_user_created` indeksi + migrasyon
- [x] `AppConfig` → `ServerConfig` + `UserCredentials` + `RunOptions` ayrımı
- [x] `AppConfig.public_capabilities()` — sır sızdırmayan yetenek keşfi
- [x] `src/jobs/`: `JobRunner` protokolü + `InProcessJobRunner`
- [x] 34 yeni test (toplam **163**, hepsi geçiyor)

**Uygulama notu — config ayrımı çoklu kalıtımla yapıldı.** `AppConfig(ServerConfig,
UserCredentials, RunOptions)` üç grubun alanlarını birleştiriyor; servis ve sağlayıcı
katmanı bugünkü gibi tek bir config nesnesi alıyor, **2.600 satır hiç değişmedi**.
API katmanı `AppConfig.compose(server, credentials, options)` ile her istek için farklı
bir bileşim kurabilir.

Doğrulandı: mevcut `data/cache/app.db` sorunsuz migrate oldu (24 kayıt `local`
kullanıcısına atandı), geçmişten tamamlanmış bir çalıştırma geri okunabiliyor.

### Faz 1 — FastAPI katmanı (Streamlit hâlâ çalışıyor)

- [ ] `api/` iskeleti, CORS, hata işleyicileri
- [ ] `POST /api/runs` + `GET /api/runs/{id}/events` (SSE) + `GET /api/runs/{id}`
- [ ] `GET /api/config` — yetenek keşfi
- [ ] `get_current_user` **stub'ı**: şimdilik daima `"local"` döner
- [ ] OpenAPI şeması üretimi
- [ ] API entegrasyon testleri (`httpx.AsyncClient`)

Bu noktada Streamlit ve API aynı servisleri çağırıyor; ikisi de çalışıyor.

### Faz 2 — React arayüzü

- [ ] Vite + React + TS iskeleti, OpenAPI'den tip üretimi
- [ ] Çalıştırma formu (konu, dil, zorluk, süre, tazelik, İngilizce, ASR)
- [ ] SSE ile canlı ilerleme (faz adı + yüzde + n/m)
- [ ] Sonuç kartları: güven puanı, gerekçe, metadata dökümü, zayıf eşleşme etiketi
- [ ] Alt konu tanılama tablosu
- [ ] Geçmiş sayfası
- [ ] `theme.py` paletini CSS değişkenlerine taşı, açık/koyu tema
- [ ] JSON / Markdown indirme

### Faz 3 — OAuth yeniden yazımı

Tek gerçek yeniden yazım burada.

- [ ] `run_local_server` çağrısını kaldır
- [ ] `GET /api/auth/youtube/start` → Google onay URL'i (state parametresiyle CSRF koruması)
- [ ] `GET /api/auth/youtube/callback` → kod → token, kullanıcıya bağla
- [ ] Token'ı dosya yerine şifreli olarak DB'de sakla
- [ ] Yayınlama akışını build'den ayır (`POST /api/runs/{id}/publish`)

### Faz 4 — Streamlit'i emekliye ayır

- [ ] `app.py` ve `src/ui/` sil
- [ ] `streamlit` bağımlılığını kaldır
- [ ] README ve `.env.example` güncelle

### Faz 5+ — Çok kullanıcıya geçiş (ihtiyaç doğduğunda)

- [ ] Gerçek kimlik doğrulama (`get_current_user` stub'ının yerine)
- [ ] Kullanıcı başına anahtar yönetimi arayüzü + şifreleme
- [ ] `CeleryJobRunner` + Redis
- [ ] Kullanıcı başına oran sınırlama
- [ ] `provider_health` kapsamını gözden geçir (aşağıya bakınız)

---

## 5. Bilinçli olarak ERTELENENLER

Şimdi yapmamak, sonra yapmayı zorlaştırmıyor:

| Erteleniyor | Neden güvenli |
|---|---|
| Gerçek kimlik doğrulama | `get_current_user` stub'ı arkasında; imza değişmez |
| Redis / Celery | `JobRunner` arayüzü arkasında |
| Oran sınırlama middleware | Çapraz kesen katman, sonradan eklenir |
| Yatay ölçekleme | SQLite → Postgres geçişi ayrı bir iş |
| Anahtar şifreleme | Faz 0'da alan hazır; şifreleme Faz 5'te |

---

## 6. Riskler ve açık sorular

**`provider_health` global.** Şu an tek kullanıcı olduğu için doğru davranış. Çok
kullanıcıda bir kullanıcının 429'u herkesi kilitler. Ama BYOK ile kullanıcılar farklı
anahtar kullanacağı için YouTube *API* sınırları ayrışır; ayrışmayan şey sunucu IP'sine
bağlı `yt-dlp` ve transkript sınırları. Karar gerekiyor: cooldown global mi kalsın
(korumacı) yoksa kullanıcı başına mı olsun (adil ama IP'yi yakar)?

**SSE bağlantı kopması.** Uzun ASR koşularında istemci kopabilir. Durum SQLite'ta zaten
tutuluyor; yeniden bağlanınca son bilinen ilerlemeden devam edilmeli. `Last-Event-ID`
başlığı ile çözülür.

**`os.environ` mutasyonu.** `KMP_DUPLICATE_LIB_OK` import zamanında ayarlanıyor
(`app.py` ve `build_playlist`). ASGI worker'larında süreç genelinde etkili — gözden
geçirilmeli.

**ASR ve sunucu kaynakları.** Whisper CPU'da video başına 90–120 sn. Çok kullanıcıda
ayrı bir worker havuzu ve sıkı kota gerekir; muhtemelen ücretli katman özelliği olmalı.

**Dosya sistemi bağımlılığı.** `data/runs/<run_id>/` altında JSON, Markdown, log.
Çok sunuculu dağıtımda nesne depolamaya (S3 vb.) taşınmalı.

---

## 7. Sonraki adım

Faz 0 ile başlanır — davranış değişmez, testler geçmeye devam eder, Streamlit çalışır.
İçindeki en değerli iki iş: **config ayrımı** ve **`user_id` kolonu**. İkisi de bugün
ucuz, sonra pahalı.
