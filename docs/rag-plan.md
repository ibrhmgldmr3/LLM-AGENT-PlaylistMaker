# Öğrenme Alanı (RAG) — Tasarım ve Ölçümler

**Hedef:** Kullanıcı, ürettiği playlist'lerin videolarını ve kendi dokümanlarını tek bir
havuzda toplayıp o havuza soru sorabilsin. **Yanıt yalnızca kaynaklara dayanır; kaynakta
yoksa "bulamadım" der.**

---

## 1. Neden yeni bir şey

Proje halihazırda transkriptleri çekiyordu ama onları yalnızca **sıralama** için ve
(opsiyonel `ENABLE_STUDY_NOTES` ile) video başına **tek bir çalışma notu** için
kullanıyordu. O yol bilinçli olarak RAG değildi ve gerekçesi
`playlist_service._generate_study_notes` docstring'inde hâlâ yazılı: tek transkript
(ortanca ~16.000 krkt) bağlam penceresine sığıyor, parçalama/vektör depo o gün olmayan
bir sorunu çözerdi.

**Değişen şey kaynak sayısı.** Bir öğrenme alanı onlarca video + doküman taşıyor;
toplamı hiçbir bağlam penceresine sığmıyor. O karar bugün de doğru, kapsamı değişti.

---

## 2. Bugünkü mimari

```
Kaynak ekleme                     Soru sorma
─────────────                     ──────────
run → transcript_service          soru
      (mevcut yol, onbellekli)      │
        │                           ├─► FTS5 leksik  ─┐
doküman → document_parser           │                 ├─► RRF ─► kapılar ─► yanıt
        │                           └─► embedding    ─┘
        ▼                              (kosinüs)
     chunking
        │
        ▼
   chunk + chunk_fts + chunk_embedding   (hepsi SQLite)
```

**Vektör veritabanı yok.** Alan başına birkaç bin parça; 2.500 × 3072 float32 ≈ 30 MB ve
numpy ile kosinüs milisaniyelerle ölçülüyor. Chroma/FAISS/pgvector eklemek bugün olmayan
bir ölçek sorununu çözmek olurdu.

**Yeni transkript yolu yok.** `rag_service.ingest_run`, mevcut
`transcript_service.get_transcript` çağırıyor; sağlayıcı sırası, 30 günlük önbellek,
soğutma ve hız sınırı mantığının tamamı olduğu gibi devralınıyor.

**Yeni iş/akış altyapısı yok.** İçeri alma, mevcut `InProcessJobRunner` ve `api/sse.py`
üzerinden yürüyor (olay günlüğü + `Last-Event-ID` devamlılığı Faz 2b'de çözülmüştü).

---

## 3. Üç kaçınma kapısı

Özelliğin asıl vaadi cevap üretmek değil, **cevabı olmayan soruya cevap üretmemek.**
Bir dil modeli istendiğinde her zaman inandırıcı bir şey yazabilir, bu yüzden üç
bağımsız kapı var (`src/services/rag_service.py`):

| # | Kapı | Ne yapar | Maliyet |
|---|---|---|---|
| 1 | **Erişim eşiği** | En iyi aday hem leksik eşleşmesiz hem `RAG_MIN_SIMILARITY` altındaysa **LLM hiç çağrılmaz** | Sıfır, deterministik |
| 2 | **Üretim şeması** | Model `answered` + `used_chunk_ids` döndürmek zorunda | Bir çağrı |
| 3 | **Alıntı doğrulaması** | Verilen numaralar, bağlama **gerçekten konulanlarla** kesiştirilir; kesişim boşsa yanıt düşürülür | Sıfır |

Üçüncüsü pazarlık konusu değil: şema bir alanın **varlığını** zorlar, **içeriğinin
doğruluğunu** değil. `tests/test_rag_abstention.py` üçünü de kilitliyor — en kritik test,
alakasız soruda sahte sağlayıcının **çağrı sayacının artmadığını** doğrulayan test.

---

## 4. Ölçülen değerler

Bu bölüm tahmin değil, çalıştırılmış ölçüm.

### Kaçınma eşiği (`RAG_MIN_SIMILARITY`)

`gemini-embedding-001`, 3 parça × 7 soru:

| | Aralık |
|---|---|
| İlgili sorular | **0.804 – 0.852** |
| Alakasız sorular | **0.496 – 0.542** |

Plan `0.55` ile yazılmıştı ve **yanlıştı**: alakasız tavanına yalnızca 0.008 kalıyordu,
yani kapı pratikte hiç kapanmıyordu. Bugünkü değer **0.70** ve iki kümenin arasındaki
0.26'lık boşluğa her iki yandan paylı oturuyor.

> **Değer modele özgüdür.** Gemini embedding modellerinde iki *alakasız* metnin kosinüsü
> bile ~0.5 tabanında kalıyor (vektörler dar bir koni içinde). "0.55 makul görünüyor"
> tahmini tam da bu yüzden tutmadı. Başka bir modele geçilirse yeniden ölçülmeli;
> `rag_service` eşiğin altında kalıp reddedilen sorguların en iyi skorunu bunun için
> logluyor.

### Embedding modeli

`text-embedding-004` **artık yok** (404 NOT_FOUND, v1beta). Bu anahtarda `embedContent`
destekleyen modeller: `gemini-embedding-001` (kararlı, 3072 boyut),
`gemini-embedding-2`, `gemini-embedding-2-preview`.

Üretim tarafındaki `FALLBACK_MODELS` deseninin **karşılığı burada bilerek yok**: bir
alanın yarısı bir modelle yarısı diğeriyle gömülürse aralarındaki kosinüs anlamsızlaşır
ve arama sessizce bozulur. Model bulunamazsa hata yükseliyor.

### Türkçe leksik eşleşme

İki ayrı sorun ölçüldü ve ikisi de düzeltildi:

1. FTS5'in `unicode61` tokenizer'ı Türkçe `ı/ğ/ş`'yi indirgemiyor → depoya
   `text_utils.search_key()` çıktısı da yazılıyor, sorgu **aynı fonksiyondan** geçiyor.
2. FTS5 tokenları **tam** eşliyor → "güncelleme" arayan bir sorgu, metinde
   "güncellemesi" geçtiği halde **hiçbir şey bulmuyordu**. `build_fts_query()` artık
   `_MIN_STEM_LENGTH`ten uzun tokenlara önek (`*`) ekliyor — sıralamadaki `tokens_match`
   gövdeleyicisiyle aynı kural.

---

## 5. Bilinçli kararlar

| Karar | Gerekçe |
|---|---|
| Zaman/sayfa **uydurulmaz** | Orantıyla tahmin edilen bir saniye kullanıcıyı alakasız yere götürür. Segment yoksa `start_sec=None` ve alıntı saniye göstermez |
| Transkript önbelleği **geçersizleştirilmedi** | 30 günlük TTL'i yalnızca zaman damgası uğruna sıfırlamak yüzlerce çağrı harcamak olurdu; eski kayıtlar `segments=[]` ile okunuyor |
| Embedding çağrıları `units=0` | `api_usage.units` YouTube kota bütçesinin kaynağı ve sağlayıcıya göre ayrışmıyor; sıfırdan büyük yazmak kullanıcıların çalıştırma hakkını alakasız sebeple bitirirdi. **Çağrı sayısı** yine de tutuluyor |
| Anlamsal yol çökerse **leksik devam** | Embedding ağ üstünden gidiyor; bedeli "daha zayıf arama" olmalı, "hiç arama yok" değil. Canlı doğrulamada bu yol gerçekten devreye girdi |
| `no_text` ≠ `failed` | Taranmış PDF ya da transkriptsiz video geçerli bir kaynaktır, yalnızca aranabilir metni yoktur. Kullanıcı **neyin aranamayacağını** bilmeli |
| `answered=false` → HTTP **200** | Özelliğin vaadi bu; hata kodu yapmak arayüzde kırmızı kutu demek ve kullanıcı sistemi bozuk sanardı |
| Başkasının alanı → **404** | 403 vermek alanın varlığını sızdırırdı (`runs.py:_owned_handle` ile aynı gerekçe) |

---

## 6. Kapsam dışı

| Dışarıda | Neden |
|---|---|
| Vektör veritabanı | Ölçek gerektirmiyor (§2) |
| Yeniden sıralama (cross-encoder) | Hibrit füzyon önce ölçülmeli; ikinci model çağrısı veri olmadan eklenemez |
| Taranmış PDF için OCR | Ayrı bir bağımlılık sınıfı. Bugün açıkça "metin çıkarılamadı" deniyor |
| Çok turlu sohbet hafızası | Takip soruları sorgu yeniden yazımı gerektirir, ayrı problem |
| Alanlar arası paylaşım | Yeni bir yetki modeli demek |

---

## 7. Sıradaki somut adım

Kalibrasyon **gerçek kullanımla** sürmeli. `RAG_MIN_SIMILARITY=0.70` yedi soruluk bir
ölçümden geliyor; asıl soru gerçek kullanıcı sorularında yanlış-"bulamadım" oranının ne
olduğu. `rag_service` reddedilen her sorgunun en iyi skorunu logluyor — o satırlara
bakılarak ayarlanmalı, tahminle değil.

`docs/react-migration-plan.md` §6'nın eşzamanlılık ayarı için koyduğu kural burada da
geçerli: **veri toplamak, kod yazmak değil.**
