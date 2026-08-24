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

## 2. Belirleyici karar: anahtar sahipliği (BYOK → paylaşımlı)

> **Bu bölüm bir kez tersine döndü.** Plan BYOK ile yazıldı ve Faz 0–4 o varsayımla
> uygulandı; `33ff038` ile paylaşımlı sunucu anahtarına geçildi. Aşağıdaki kota
> matematiği **hâlâ geçerli** — değişen tek şey, faturayı kimin ödediği.

YouTube Data API kotası **proje başına günde 10.000 birim**. Bir çalıştırmanın maliyeti:

| Çağrı | Adet (6 alt konu, iki dilli) | Birim | Toplam |
|---|---|---|---|
| `search.list` | 12 | 100 | 1.200 |
| `videos.list` | ~12 | 1 | ~12 |
| `channels.list` | ~12 | 1 | ~12 |
| | | | **≈ 1.224** |

Paylaşımlı anahtarla **günde ~8 çalıştırma, tüm kullanıcılar toplamı.**

### Önce: BYOK (uygulandı, sonra geri alındı)

Bu matematik yüzünden ilk karar "her kullanıcı kendi Gemini ve YouTube anahtarını
sağlar" oldu. Kimlik bilgileri şifreli olarak veritabanında tutuldu, `/api/credentials`
ucu ve bir "Ayarlar" ekranı yazıldı.

### Sonra: paylaşımlı sunucu anahtarı (bugünkü durum)

Kullanıcıdan Google Cloud projesi açıp iki ayrı API anahtarı üretmesini istemek,
hedeflenen kullanım için giriş engeli olarak fazla yüksek bulundu. Bugün **kullanıcı
hiçbir anahtar girmiyor**; LLM (Gemini/Together) ve YouTube arama anahtarları
`.env`'den, tüm kullanıcılar için ortak gelir.

Kaldırılanlar: `/api/credentials` ucu, `CredentialsPanel` ekranı, `user_credential`
tablosu ve `UserCredentials` içindeki anahtar alanları.

**Kotanın bedeli artık sunucu sahibinde.** Bunu sınırlayan tek mekanizma
`MAX_RUNS_PER_USER_PER_DAY`; `multi_user` modda 0 (sınırsız) bırakılırsa giriş yapan
tek bir kullanıcı günü bitirebilir. Varsayılan 0 olduğu için bu durum kendiliğinden
oluşuyor — `api/main.py` açılışta bu kombinasyonu uyarı olarak loglar.

Şifreleme altyapısı (`SecretBox`) duruyor: YouTube **OAuth jetonları** hâlâ kullanıcı
başına ve şifreli saklanıyor. Değişen, API *anahtarlarının* kullanıcıya ait olması.

### Config bölünmesi (bugünkü sayılar)

| Grup | Adet | Nereye gider |
|---|---|---|
| **Kullanıcı sırrı** | 4 | `UserCredentials` — API'den düzenlenmiyor |
| **Çalıştırma seçeneği** | 13 | İstek gövdesi / kullanıcı tercihi |
| **Sunucu ayarı** | 37 | `.env`, tüm sunucu için ortak |

Üçe bölme kararı BYOK geri alınınca da **değerini korudu**: `AppConfig`'in tek global
nesne olmaktan çıkması, istek başına seçeneklerin gövdeden ezilebilmesini ve sırların
yanıtlara sızmamasını sağlıyor. Geri dönüşte yalnızca alanlar `UserCredentials`'tan
`ServerConfig`'e taşındı — mimari değişmedi.

Kullanıcı sırrı olarak kalanlar: `YOUTUBE_OAUTH_TOKEN_FILE`, `YTDLP_PROXY`,
`YTDLP_COOKIES_FROM_BROWSER`, `YTDLP_COOKIES_FILE`.

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
GET    /api/auth/youtube/start   → Google onay URL'i döndür
GET    /api/auth/youtube/callback→ kodu token'a çevir, kullanıcıya bağla
GET    /api/auth/me              → oturum durumu (401 değil, signed_in: false)
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

### Faz 1 — FastAPI katmanı ✅ TAMAMLANDI

- [x] `api/` iskeleti, CORS (Vite `:5173`), sağlayıcı hatalarını HTTP'ye eşleyen işleyiciler
- [x] `POST /api/runs` → 202 + `run_id` (**ölçüldü: 207 ms, bloklamıyor**)
- [x] `GET /api/runs/{id}/events` — SSE, heartbeat'li, bitmiş iş için anında kapanır
- [x] `GET /api/runs/{id}`, `/status`, `DELETE`, `GET /api/runs` (sayfalı), export indirme
- [x] `GET /api/config` — yetenek keşfi, **sır sızdırmadığı testle kilitli**
- [x] `get_current_user` **stub'ı**: daima `"local"` döner
- [x] OpenAPI şeması üretiliyor (React tipleri buradan gelecek)
- [x] 17 API testi (toplam **180**, hepsi geçiyor)

**Canlı doğrulama** (gerçek uvicorn + gerçek YouTube/Gemini): POST 202 döndü,
SSE 11 ilerleme olayı akıttı (monoton), sonuç okunabildi, geçmiş listelendi,
Markdown indirildi, bilinmeyen çalıştırma 404 verdi, anahtar sızmadı.

Streamlit ve API aynı servisleri çağırıyor; ikisi de çalışıyor.

```bash
uvicorn api.main:app --reload --port 8000   # API
streamlit run app.py                        # eski arayüz, hâlâ çalışıyor
```

**Bilinen sınır:** her açık SSE akışı bir thread tutuyor (`JobRunner.events()`
bloklayan kuyruk üzerinde çalışıyor, Starlette senkron üreticiyi thread havuzunda
döndürüyor). Tek instance için kabul edilebilir; `CeleryJobRunner` ile birlikte
gözden geçirilecek. `Last-Event-ID` ile tam devam ettirme de Faz 2'ye bırakıldı —
şu an yeniden bağlanan istemci bitmiş işin son durumunu alıyor, ara olayları değil.

### Faz 2 — React arayüzü ✅ TAMAMLANDI (çekirdek akış)

- [x] Vite + React 18 + TS iskeleti, `npm run typecheck` ve `build` temiz
- [x] Vite proxy: `/api` → `:8000` (geliştirmede CORS'a hiç takılmıyoruz)
- [x] Çalıştırma formu (konu, dil, zorluk, süre, tazelik, İngilizce, ASR)
- [x] `useRunStream` — `EventSource` ile canlı ilerleme
- [x] Sonuç kartları: güven puanı, gerekçe, metadata dökümü, **zayıf eşleşme etiketi**
- [x] Alt konu tanılaması (açılır, kısa liste tablosuyla)
- [x] Geçmiş sekmesi: sayfalama, aç, sil
- [x] `theme.py` paleti CSS değişkenlerine taşındı, açık/koyu tema
- [x] JSON / Markdown indirme

**Tarayıcıda uçtan uca doğrulandı** ("Kalman filtresi ile sensör füzyonu"):
form → `POST` 202 → SSE ilerleme (%2 → %40 → %100) → 6/6 alt konu dolu,
52 videoluk ortak havuz, 6 öneri, 5'i zayıf eşleşme olarak etiketlendi.
Geçmiş 10 kayıt listeledi, karanlık tema geçişi çalıştı.

### Faz 2b — SSE dayanıklılığı ✅ TAMAMLANDI

Faz 2 sonrası bir **hata** bulundu ve düzeltildi: olaylar tek bir `queue.Queue`
üzerinden **yıkıcı** okunuyordu. İki sekme aynı çalıştırmayı izlediğinde olaylar
aralarında bölünüyordu (A: `m1,m2,m4,m6` / B: `m3,m5`) ve bir dinleyici
yakalanmamış `queue.Empty` ile ölüyordu.

- [x] Kuyruk → **eklemeli olay günlüğü** (her abonenin kendi imleci)
- [x] Her SSE olayı `id:` taşıyor
- [x] `Last-Event-ID` ile devam: yeniden bağlanan istemci yalnızca kaçırdıklarını alır
- [x] Çoklu abone: her sekme tüm olayları görür
- [x] 8 yeni test

Ayarlar ekranı (kullanıcı anahtarları) o gün **bilerek Faz 5'e bırakılmıştı**: kimlik
doğrulama ve şifreleme olmadan API anahtarı yazan bir ekran güvenlik açığıdır. Faz 5'te
yapıldı, ardından paylaşımlı anahtara geçilince tamamen **kaldırıldı** (§2) — bugün
böyle bir ekran yok.

### Faz 3 — OAuth yeniden yazımı ✅ TAMAMLANDI

- [x] `run_local_server` yayınlama yolundan kaldırıldı
- [x] `GET /api/auth/youtube/start` → Google onay URL'i, tek kullanımlık `state` ile CSRF koruması
- [x] `GET /api/auth/youtube/callback` → kod → jeton → **kullanıcı başına** saklanır
- [x] `GET /api/auth/youtube/status`, `DELETE /api/auth/youtube`
- [x] Jeton dosya yerine `oauth_token` tablosunda (kullanıcı + sağlayıcı anahtarlı)
- [x] Yenilenen jeton geri yazılıyor (`on_token_refresh`)
- [x] `POST /api/runs/{id}/publish` — **build'den ayrı**
- [x] React: `PublishPanel` (bağla / yayınla / ayır)
- [x] 17 test

**Streamlit bozulmadı:** `token_json` verilmezse eski dosya tabanlı akışa dönülüyor.

Canlı doğrulandı: yetkilendirme URL'i üretiliyor, `client_secret` URL'e sızmıyor,
bağlanmadan yayınlama reddediliyor.

> **Kurulum notu:** Google Cloud Console'da OAuth istemcisine
> `http://localhost:8000/api/auth/youtube/callback` adresi *authorized redirect URI*
> olarak eklenmeli. İstemci tipi **Web application** olmalı (Desktop değil).

### Faz 4 — Streamlit emekliye ayrıldı ✅ TAMAMLANDI

- [x] `app.py` (163 satır) ve `src/ui/` (338 satır) silindi
- [x] `streamlit` bağımlılığı kaldırıldı
- [x] **FastAPI derlenmiş React'i servis ediyor** — üretimde tek süreç yeter
- [x] README ve `.env.example` güncellendi (0 Streamlit referansı kaldı)

Canlı doğrulandı: `uvicorn api.main:app` tek başına `/` üzerinden arayüzü,
`/api/*` üzerinden API'yi, `/docs` üzerinden OpenAPI'yi servis ediyor. Aynı
köken olduğu için CORS'a gerek kalmıyor.

### Faz 5 — Kısmen tamamlandı, gerisi bilinçli ertelendi

**Yapıldı (bugün doğru olan):**

- [x] **Jetonlar diskte şifreli** — `SECRET_ENCRYPTION_KEY` ayarlıysa `oauth_token`
      tablosu HMAC-SHA256 anahtar akışı + kurcalama etiketiyle şifrelenir.
      Harici bağımlılık yok. Anahtar sonradan eklenebilir: mevcut düz kayıtlar
      okunmaya devam eder, sonraki yazımda şifrelenir.
- [x] `get_current_user(request)` — imzası gerçek kimlik doğrulamaya hazır;
      o gün yalnızca gövdesi değişecek, hiçbir rota elden geçmeyecek.

**Bilinçli ERTELENDİ (bugün yapmak yanlış olurdu):**

| Erteleniyor | Neden |
|---|---|
| ~~Gerçek kimlik doğrulama sağlayıcısı~~ | **Yapıldı**: Google OAuth girişi + `AUTH_MODE=multi_user` + oturum çerezi (`api/routers/auth.py`). `get_current_user` gövdesi değişti, hiçbir rota elden geçmedi — vaat tutuldu |
| ~~Kullanıcı anahtarı yönetim ekranı~~ | Yapıldı, sonra **kaldırıldı** (§2): anahtarlar paylaşımlı, düzenlenecek bir şey kalmadı |
| `CeleryJobRunner` + Redis | `JobRunner` arayüzü hazır; kullanıcı yokken altyapı kurmak erken optimizasyon |
| ~~Kullanıcı başına oran sınırlama~~ | **Yapıldı**: `MAX_RUNS_PER_USER_PER_DAY` (günlük kota, tek işlemde sayılıp yazılır). Paylaşımlı anahtara geçince zorunlu hale geldi (§2) |
| `provider_health` kapsamı | Paylaşımlı anahtarla YouTube API sınırları **ayrışmıyor** (§2), yani global cooldown doğru davranış; kullanıcı başına ayırmak sunucu kotasını korumasız bırakırdı |

Bunların hepsi **arayüzlerin arkasında** duruyor; sıra geldiğinde eklenecekler,
yeniden yazım gerekmeyecek.

---

## 5. Bilinçli olarak ERTELENENLER

Şimdi yapmamak, sonra yapmayı zorlaştırmıyor:

| Erteleniyor | Neden güvenli |
|---|---|
| ~~Gerçek kimlik doğrulama~~ | **Yapıldı** (Google OAuth); imza gerçekten değişmedi |
| Redis / Celery | `JobRunner` arayüzü arkasında |
| Oran sınırlama middleware | Hâlâ yok; günlük kota (`MAX_RUNS_PER_USER_PER_DAY`) şimdilik yerini tutuyor |
| Yatay ölçekleme | SQLite → Postgres geçişi ayrı bir iş |
| ~~Anahtar şifreleme~~ | Yapıldı (`SecretBox`). Kapsamı daraldı: artık API anahtarlarını değil, kullanıcı başına **OAuth jetonlarını** şifreliyor (§2) |

---

## 6. Riskler ve açık sorular

**Sağlayıcı soğuması artık sunucu geneli** (`SERVER_SCOPE`). Bu soru BYOK varsayımıyla
açılmıştı ve kapsam bir ara kullanıcı başına çekilmişti: kullanıcılar farklı anahtar
kullansa YouTube *API* sınırları gerçekten ayrışırdı. Anahtar paylaşımlı olunca (§2)
hiçbiri ayrışmıyor — kota da IP de tek ve ortak.

Ölçüldü: kullanıcı başına kapsamda **ikinci kullanıcı hiç korunmuyordu.** Ali `yt_dlp`'de
soğumaya girse bile Veli aynı sunucu IP'sinden gidip aynı sınıra takılıyor ve engeli
uzatıyordu. Bir kullanıcının 429'u artık herkesi durduruyor; bu bir maliyet değil, doğru
davranış — zaten herkes aynı IP'den çıkıyor. Adaleti sağlayan mekanizma soğuma değil
`MAX_RUNS_PER_USER_PER_DAY`.

Kapsam değişince ortaya çıkan bir yarış da kapatıldı: başarılı bir çağrı artık süresi
dolmamış bir hız-sınırı soğumasını **iptal etmiyor** (eskiden B çalıştırmasının başarısı
A'nın taze soğumasını siliyordu).

**Açık kalan:** eşzamanlılık. 2 çalıştırma × 4 işçi = tek IP'den 8 eşzamanlı istek.
Soğuma bir *tepki*; sınıra hiç girmemek için asıl ayar bu. `GET /api/admin/usage`
artık günlük `rate_limited` / `failure` / `cooldown` sayılarını döndürüyor — ayar
tahminle değil bu sayılara bakılarak değiştirilmeli.

**Bugünkü veri: yok.** `provider_event` tablosu boş; sayaç eklendiğinden bu yana tek
bir çalıştırma yapıldı (1.224 birim) ve hiçbir sınıra takılmadı. Yani ayarı şimdi
değiştirmek, tam da bu maddenin yasakladığı tahmin olurdu. Sayaçların gerçekten
yazdığı 5 testle kilitlendi (`tests/test_provider_events.py`) — biri 429'u sağlayıcı
seviyesinde atıp sayacı servis yolunun sonundan okuyor. Bir ölçüm aracının en kötü
arızası sessizce hiçbir şey kaydetmemesidir: boş tablo "sorun yok" gibi okunur ve
arıza tam da karar verilecek anda görünmez olur.

**~~SSE bağlantı kopması~~ — çözüldü (Faz 2b).** Bu madde bir süre burada, "çözülür"
kipinde, çoktan çözülmüş olarak durdu. Olay günlüğü eklemeli; her olay `id:` taşıyor ve
yeniden bağlanan istemci `Last-Event-ID` ile yalnızca kaçırdıklarını alıyor
(`api/sse.py`, `api/routers/runs.py`). Geçersiz başlık baştan başlatıyor.

**~~`os.environ` mutasyonu~~ — çözüldü.** Gözden geçirilince ortaya çıkan şey bir
ölçekleme riski değil, **ölü bir ayardı**: `KMP_DUPLICATE_LIB_OK`, `src/__init__.py`
içinde koşulsuz kuruluyordu. O modül config okunmadan önce yükleniyor, üstelik
`setdefault` olduğu için sonraki (config'e bakan) kontrolleri de etkisiz bırakıyordu —
yani `ALLOW_UNSAFE_OPENMP_WORKAROUND=false` diyen kullanıcının seçimi Windows'ta
sessizce eziliyordu. `api/main.py` lifespan'indeki kopya zaten fazlalıktı (paket importu
çoktan çalışmış oluyor).

İkisi de kaldırıldı. Bayrak artık yalnızca `build_playlist` girişinde ve
`FasterWhisperProvider.transcribe` içinde, **config'e bakarak** kuruluyor; `faster_whisper`
importu `_load_model` içinde tembel olduğu için bu noktalar hâlâ yeterince erken.
4 test kilitliyor (`tests/test_openmp_workaround.py`), biri `os.name`'i sahteleyerek
regresyonu Windows dışında da görünür kılıyor.

**ASR ve sunucu kaynakları.** Whisper CPU'da video başına 90–120 sn. Çok kullanıcıda
ayrı bir worker havuzu ve sıkı kota gerekir; muhtemelen ücretli katman özelliği olmalı.

**Dosya sistemi bağımlılığı.** `data/runs/<run_id>/` altında JSON, Markdown, log.
Çok sunuculu dağıtımda nesne depolamaya (S3 vb.) taşınmalı.

---

## 7. Bugünkü durum ve sonraki adım

**Faz 0–4 tamamlandı, Faz 5 kısmen tamamlandı.** Yeni bir faz beklenmiyor; proje şu an
**Faz 5 sonrası bakım ve sertleştirme** evresinde. Kalan iş mimari eklemek değil,
üretimde biriken gerçek kullanım verisine göre ayar kalibre etmek (aşağıya bakın).

### Faz 5'te fiilen tamamlananlar

- [x] Google OAuth girişi + `AUTH_MODE=multi_user` + oturum çerezi (`api/routers/auth.py`)
      — `get_current_user`'ın imzası baştan buna hazırdı, hiçbir rota elden geçmedi
- [x] OAuth jetonları diskte şifreli (`SecretBox`, `SECRET_ENCRYPTION_KEY` ayarlıysa)
- [x] Kullanıcı başına günlük kota (`MAX_RUNS_PER_USER_PER_DAY`) ve servis geneli kota
      tavanı (`MAX_UNITS_PER_DAY`) — kabul kontrolü ve yazım aynı transaction'da,
      kota admission anında rezerve ediliyor
- [x] `GET /api/admin/usage` — günlük tüketim, uç nokta kırılımı, kullanıcı bazlı
      toplamlar, aktif soğumalar, rate-limit/hata/cooldown sayaçları (`api/routers/admin.py`)
- [x] Sağlayıcı soğuması sunucu geneline çekildi (`SERVER_SCOPE`); başarılı bir çağrı artık
      süresi dolmamış bir rate-limit soğumasını silmiyor
- [x] Bekleyen OAuth `state` sözlüğüne tavan — sızıntı önleme (`09d19e4`)
- [x] OpenMP bayrağının kullanıcı tercihini Windows'ta sessizce ezmesi düzeltildi (`b34cf68`)
- [x] Günlük olay sayaçları (`provider_event`) gerçekten yazdığını doğrulayan test kilidi
      (`b2d3cef`, `tests/test_provider_events.py`)

### Belirleyici tersine dönüş: BYOK → paylaşımlı sunucu anahtarı

Plan BYOK (her kullanıcı kendi Gemini/YouTube anahtarını girer) varsayımıyla yazıldı ve
Faz 0–4 o varsayımla uygulandı. `33ff038` bunu tersine çevirdi: kullanıcıdan Google Cloud
projesi açıp iki ayrı API anahtarı üretmesini istemek, hedeflenen kullanım için giriş
engeli olarak fazla yüksek bulundu. Bugün kullanıcı hiçbir anahtar girmiyor — LLM ve
YouTube anahtarları `.env`'den, sunucu geneli ve tüm kullanıcılar için ortak. Bu değişimle
birlikte `/api/credentials` ucu, `CredentialsPanel` ekranı ve `user_credential` tablosu
kaldırıldı. Kota matematiği (§2) değişmedi; değişen tek şey faturayı kimin ödediği —
şimdi sunucu sahibi, o yüzden yukarıdaki günlük kota tavanları zorunlu hale geldi.

### Bilinçli olarak ertelenenler (bugün yapmak yanlış olurdu)

| Erteleniyor | Neden |
|---|---|
| `CeleryJobRunner` + Redis | `JobRunner` arayüzü hazır ve bekliyor; kullanıcı trafiği yokken dağıtık kuyruk kurmak erken optimizasyon olurdu |
| Rate-limit middleware | Günlük kota (`MAX_RUNS_PER_USER_PER_DAY`) şimdilik yerini tutuyor; gerçek bir eşzamanlılık middleware'i, aşağıdaki kalibrasyon verisi toplanmadan yazılırsa tahminle ayarlanmış olur — tam da §6'nın yasakladığı şey |
| SQLite → Postgres | Yatay ölçekleme ayrı bir iş; tek instance için SQLite yeterli, dosya sistemi bağımlılığı (`data/runs/`) zaten aynı sınırı taşıyor ve birlikte ele alınmalı |

### Sıradaki somut adım: veri toplamak, kod yazmak değil

Açık kalan tek mühendislik sorusu — eşzamanlılık ayarı (`MAX_SEARCH_WORKERS`,
`MAX_TRANSCRIPT_WORKERS`, ikisi de bugün `4`) — bir kod değişikliğiyle değil, **ölçüm
eksikliğiyle** bekliyor (§6). `provider_event` tablosu neredeyse boş; sayaç
eklendiğinden beri tek çalıştırma yapıldı ve hiçbir sınıra takılmadı. Ayarı şimdi
değiştirmek bu maddenin kendi yasakladığı tahmin olurdu. İzleme mekanizması hazır
(`GET /api/admin/usage`, README → "Watching it"); yapılacak olan sunucuyu gerçek trafikle
çalıştırıp o uca zaman içinde bakmak.
