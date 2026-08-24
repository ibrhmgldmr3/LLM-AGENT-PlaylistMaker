# Derslik — görsel sistem

Bu belge **kurulmuş olan** arayüzü tarif eder, niyeti değil. Kaynak tek yerde:
`web/src/styles.css`. Buradaki her değer oradan okunmuştur.

## Tez

Kendi kendine öğrenen birinin **dersi**; arama sonucu listesi değil. Sıralı ünite
dizisi nesnenin kendisidir. Reddedilen kategori varsayılanı: alaka puanlı, aynı
boyda video kartlarından oluşan sonsuz akış.

## Dünya: dershanenin iki yüzeyi

| Yüzey | Ne taşır |
|---|---|
| **Ders tahtası** (koyu yeşil) | Gezinme rayı, ders başlığı, hazırlık paneli, "şu an buradasın" işareti |
| **Kağıt** (sıcak yulaf) | Okunan her şey: üniteler, çalışma notları, cevaplar, gerekçeler |
| **Tebeşir kehribarı** | TEK sinyal rengi: aktif olan, sıradaki, ne kadar ilerlediğin |

Cam, bulanıklık, gradyan metin ve renkli hale yok. Derinlik gerçek gölgeyle
(ofset + yumuşak bulanıklık), ayrım saç çizgisiyle verilir.

## Renk belirteçleri

Aydınlık varsayılan; karanlık tema `:root[data-theme="dark"]` altında aynı
rollerle yeniden tanımlanır. `index.html` içindeki satır içi betik temayı ilk
boyamadan önce uygular (yanlış temanın bir kare bile görünmemesi için).

```
--ground #f0ede4   --surface #faf8f3   --surface-2 #f4f1e8   --surface-sunk #e9e5d9
--ink    #17201c   --ink-2   #4c5a53   --ink-3     #5a6862
--rule   #ddd8cb   --rule-2  #c8c2b2
--board  #1b3a31   --board-2 #24493e   --board-3   #326052
--chalk  #eff0e6   --chalk-2 #a9c0b4   --chalk-3   #a2beb1
--signal #c9861a   --signal-ink #8a5a0c  --signal-soft #f7edd7  --signal-chalk #e0a63a
--ok     #2e6b47   --danger  #9c3026
```

**Kontrast**: tüm gövde ve ipucu metinleri her iki temada ≥4.5:1 ölçüldü
(tarayıcıda hesaplandı, göz kararı değil). `--ink-3` ve `--chalk-3` bu ölçüm
sonucunda koyulaştırıldı/açıldı.

## Tipografi

İki yazı karakteri, iki farklı iş:

- **Archivo** (`--sans`) — *kurum konuşuyor*: gezinme, başlıklar, düğmeler,
  etiketler, zaman damgaları. Tabela register'ı.
- **Literata** (`--serif`) — *okuduğun malzeme*: çalışma notları, RAG cevapları,
  seçim gerekçeleri, alıntılar. Kitap register'ı.

Okuma ölçüsü `.prose` ve `.unit__why` için 68–70ch (ölçüldü). Başlıklarda
`letter-spacing: -0.018em`, büyük soruda `-0.034em`. Zaman ve sayılarda
`font-variant-numeric: tabular-nums`.

## Yerleşim

```
≥900px          <900px
┌──────┬──────┐  ┌────────────┐
│ ray  │ bar  │  │    bar     │
│(tahta├──────┤  ├────────────┤
│ 15.5 │ kağıt│  │   kağıt    │
│ rem) │      │  ├────────────┤
└──────┴──────┘  │ alt sekme  │
```

- `.body` en fazla 1180px; çalışma odasında `.body--wide` ile 1560px.
- `.room` iki sütun (içerik + sohbet), 1100px altında tek sütuna iner.
- Dokunma hedefleri <900px'te ≥44px (`.unit__num`, `.btn`, `.icon-btn`).

## Bileşen dili

| Sınıf | Rol |
|---|---|
| `.panel` / `.panel--board` | Kağıt kutu / tahta kutu |
| `.unit` | Numaralı ders; numara aynı zamanda "izledim" düğmesi |
| `.route` | Hazırlık güzergâhı — nokta + çizgi, geçmiş/şimdi/bekleyen |
| `.course-row` | Liste nesnesi (ders planı ve çalışma defteri aynı dili kullanır) |
| `.note` (`--warn`/`--danger`/`--ok`) | Uyarı kutusu; `tone` ile `role` ayrı |
| `.fold` | `<details>` üzerine kurulu kat — JS'siz açılır, Ctrl+F bulur |
| `.chip` / `.tag` | Tıklanabilir öneri / durum etiketi |
| `.meter` | İlerleme; `scaleX` ile ölçeklenir (yerleşim tetiklemez) |
| `.cite` / `.line` | Kaynak göstergesi / videonun işaretli anı |

Numaralandırma yalnızca **sıra bilgi taşıdığı için** var (ders planı sıralı bir
müfredat). Süsleme numarası, başlık üstü küçük etiket (eyebrow), aynı boy kart
ızgarası ve büyük-sayı metrik döşemesi bilinçli olarak yok.

## Hareket

Sayfanın **tek kurgulanmış anı**: üniteler sıraya dizilirken kademeli olarak
yükselerek yerine oturur (`unit-in`, 520ms, `cubic-bezier(.16,1,.3,1)`, ünite
başına 45ms gecikme, 8. üniteden sonra sabit).

Başlangıç karesi **görünür** (`opacity: .35`) ve animasyon yalnızca
`prefers-reduced-motion: no-preference` altında bağlanır: hareket çalışmasa bile
içerik okunur kalır. Hazırlık panelinde aktif adımın halkası nefes alır
(`ripple`). Bunun dışında yalnızca durum geçişleri var.

## İkonlar

`lucide-react`, tek çizgi kalınlığı, 3.5–5 birim aralığında. Emoji ve unicode
oklar ikon yerine kullanılmaz (eski arayüzdeki 🎬/📄/☀/🌙/←/→ kaldırıldı).

## Dil

Arayüz **samimi ikinci tekil** kullanır ("Ne öğrenmek istiyorsun?", "hakkın
kaldı"). Motor terimleri kullanıcıya gösterilmez:

| Motor | Arayüz |
|---|---|
| çalıştırma / run | ders planı |
| alt konu | ünite (ve ünitenin başlığı) |
| alan / space | çalışma defteri |
| parça / chunk | bölüm |
| indexed / pending / no_text / failed | Hazır / Hazırlanıyor / Metin yok — aranamaz / Eklenemedi |
| confidence 8.73/10 | (yalnızca "Neden bu video seçildi?" katında) |

Puan dökümü ve alt konu tanılaması silinmedi — katların içine alındı. Meraklı
kullanıcı bulur, yeni başlayan görmez.
