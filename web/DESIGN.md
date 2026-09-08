# Müze Etiketi — görsel sistem

Bu belge **kurulmuş olan** arayüzü tarif eder, niyeti değil. Kaynak tek yerde:
`web/src/styles.css`. Buradaki her değer oradan okunmuştur.

## Tez

Defter bir **koleksiyon**, her kaynak bir **eser**, her alıntı bir **künye**.
Reddedilen iki kategori varsayılanı: krem kâğıt + serif "çalışma defteri"
görüntüsü (bu ürünün kendi önceki dünyası) ve onun karşıtı olan siyah + neon
"yapay zekâ aracı" görüntüsü.

## Dünya: üç malzeme

| Malzeme | Ne taşır |
|---|---|
| **Duvar** (`--board`, koyu yeşil) | Gezinme rayı, üst çubuk, karşılama ve bölüm başlıkları, "şu an buradasın" |
| **Etiket** (`--surface`, beyaz) | Okunan her şey; üstünde ait olduğu salonun renginden ince bir bant |
| **Zemin** (`--ground`, galeri grisi) | Rengin görünmesi için renksiz kalır |

Markanın yeşili **değişmedi**; değişen, ne kadar yer kapladığı. Eskiden 15.5rem
genişliğinde bir gezinme şeridiydi; şimdi ray, üst çubuk ve karşılama bölgesi
aynı yüzeyin parçaları.

Cam, bulanıklık, gradyan metin ve renkli hâle yok. Köşeler keskin (3–8px).
Derinlik gerçek gölgeyle (ofset + yumuşak bulanıklık), ayrım saç çizgisiyle.

## Renk anahtardır, süs değildir

Altı **salon rengi** (`--key-1..6`) kaynakları anahtarlar. Renk sırayla
dağıtılmaz — sıralama değişince renk de değişirdi — kaynağın kimliğinden
türetilir (`src/lib/roomKey.ts`, `source_id` üzerinde deterministik hash).
Aynı kaynak her oturumda, her bileşende aynı rengi alır.

Kural ancak renk **her karşılaşmada aynıysa** işe yarar, o yüzden üç yüzeyde de
bağlıdır:

| Yüzey | Sınıf | Karşılaşma |
|---|---|---|
| Kenar bandı | `.source` | Rengin öğrenildiği yer — kaynak burada adıyla duruyor |
| Künye | `.cite` | Yanıtın dayandığı kaynak; renk adın yerine geçebiliyor |
| Transkript işareti | `.line` | Birden çok kaynaktan toplanan anlar |

Çakışma kabul ediliyor: altı renk var, bir defterde daha fazla kaynak olabilir.
Renk bir kimlik doğrulaması değil, bir hatırlatıcı.

**Okra bu kümede yok.** Okra (`--signal`) yönlendirme sinyali — "aktif olan,
sıradaki, tıklanacak olan". Aynı sarıyı bir kaynağın *kimliği* olarak da
kullanmak dünyanın tek kuralını bulanıklaştırırdı. Bu yüzden `.source--active`
ve `.line--active` kimlik rengini bırakıp konum rengine (duvar / okra) geçer:
**konum duvarın işi, kimlik salonun.**

Okunan alan **akromatik** kalır. Uzun transkript ve yanıt metni gri-siyah
mürekkeple okunur; renk yapısal kenarlarda durur.

## Renk belirteçleri

Aydınlık varsayılan; karanlık tema `:root[data-theme="dark"]` altında aynı
rollerle yeniden tanımlanır. `index.html` içindeki satır içi betik temayı ilk
boyamadan önce uygular (yanlış temanın bir kare bile görünmemesi için).

```
--ground #e8eae5   --surface #ffffff   --surface-2 #f5f6f3   --surface-sunk #dfe2dc
--ink    #14171a   --ink-2   #4e565c   --ink-3     #6f787e
--rule   #d3d7cf   --rule-2  #b9bfb6
--board  #1b3a31   --board-2 #142c25   --board-3   #326052
--chalk  #eff2ec   --chalk-2 #a9c0b4   --chalk-3   #8fae9f
--signal #b4700f   --signal-ink #8a560b  --signal-soft #f7edd9  --signal-chalk #e0a63a
--key-1  #23409e   --key-2 #0f6f6d   --key-3 #7a4a12
--key-4  #a83621   --key-5 #653a80   --key-6 #3d6a2c
--ok     #2f6d4f   --danger  #a3342a
```

Salon renkleri **kategorik**: birbirine yakın olmamaları gerekiyor, çünkü
sırasız veriler için ton skalası değil ayrık renkler kullanılır. Altısı da beyaz
etiket üzerinde 4.5:1'i geçiyor — künye metni bu renkle yazılıyor.

Karanlıkta duvar **duvar kalır** (`#1a3a30`): gece modunda markanın kaybolup her
şeyin griye dönmesi, aydınlıkta kurulan anahtarı bozardı. Salon renkleri koyu
zeminde okunacak kadar açılır (`--key-1: #8098ff` vb.).

Birincil eylem duvarın rengini **kullanmaz**: duvar bir bölge olunca yeşil zemin
üzerinde yeşil eylem görünmez oluyordu. `.btn--primary` parlak okra + neredeyse
siyah metin — hem duvarda hem galeri grisinde ~9:1.

## Tipografi

Üç yazı karakteri, üç farklı iş:

- **Archivo** (`--sans`) — *kurum konuşuyor*: gezinme, başlıklar, düğmeler,
  etiketler. Tabela register'ı.
- **Literata** (`--serif`) — *duvar metni*: yanıtlar, çalışma notları,
  transkript, alıntılar. Kitap register'ı.
- **Mono** (`--mono`) — *künye*: zaman kodu, sayfa, sayı. Ölçüm taşıdığı için
  mono, "teknik görünsün" diye değil; `font-variant-numeric: tabular-nums` ile
  rakamlar alt alta hizalanır.

Okuma ölçüsü `.prose` ve `.unit__why` için 68–70ch. Başlıklarda
`letter-spacing: -0.018em`, karşılama duvarındaki soruda `-0.042em`.

## Yerleşim

```
>=1100px          <1100px           <900px
+------+-------+  +--------------+  +--------------+
| ray  |  bar  |  |     bar      |  |     bar      |
|(duvar+-------+  +--------------+  +--------------+
| 15.5 | zemin |  |    zemin     |  |    zemin     |
| rem) |       |  |  (tek sütun) |  +--------------+
+------+-------+  +--------------+  |  alt sekme   |
                                    +--------------+
```

- `.body` en fazla 1180px; çalışma odasında `.body--wide` ile 1560px.
- `.room` iki sütun (içerik + sohbet), 1100px altında tek sütuna iner.
- Karşılama duvarı ve bölüm başlıkları kenar boşluklarının **dışına** taşar
  (negatif `margin`): bir duvar kenarlıklı bir kutu değil, bölgenin kendisidir.
- Dokunma hedefleri <900px'te >=44px = 2.75rem (`.unit__num`, `.btn`, `.icon-btn`).

## Bileşen dili

| Sınıf | Rol |
|---|---|
| `.panel` / `.panel--board` | Yüzey kutu / duvar kutu |
| `.unit` | Numaralı ders; numara aynı zamanda "izledim" düğmesi |
| `.route` | Hazırlık güzergâhı — nokta + çizgi, geçmiş/şimdi/bekleyen |
| `.course-row` | Liste nesnesi (ders planı ve defter aynı dili kullanır) |
| `.source` | Kaynak satırı — sol kenar bandı salon rengi |
| `.cite` / `.line` | Künye / videonun işaretli anı; ikisi de salon rengi |
| `.notice` (`--absent`/`--unverified`/`--outofscope`) | Yanıt verilemedi |
| `.note` (`--warn`/`--danger`/`--ok`) | Uyarı kutusu; `tone` ile `role` ayrı |
| `.fold` | `<details>` üzerine kurulu kat — JS'siz açılır, Ctrl+F bulur |
| `.chip` / `.tag` | Tıklanabilir öneri / durum etiketi |
| `.meter` | İlerleme; `scaleX` ile ölçeklenir (yerleşim tetiklemez) |

Numaralandırma yalnızca **sıra bilgi taşıdığı için** var. Süsleme numarası,
başlık üstü küçük etiket (eyebrow), aynı boy kart ızgarası ve büyük-sayı metrik
döşemesi bilinçli olarak yok.

## Yanıt verilemediğinde

Üç ayrı dil, biri diğerinin soluk hâli değil. Ayrımı sunucu `refusal` alanında
**tür** olarak veriyor (`not_found` / `unverified` / `no_content`); arayüz onu
gerekçe cümlesinden okumaz, çünkü cümle her yeniden yazıldığında ayrım sessizce
kaybolurdu.

| Tür | Sınıf | Ne diyor | Kullanıcı ne yapmalı |
|---|---|---|---|
| `not_found` | `.notice--absent` | "Bu defterde yok" | Soruyu ya da defteri değiştir |
| `unverified` | `.notice--unverified` | "Doğrulanamadı" | Aynı soruyu tekrar sor |
| `no_content` | `.notice--outofscope` | "Kapsam dışı" | Kaynak ekle |

**Kırmızı yok.** Reddetmek bu özelliğin vaadi, arızası değil; kırmızı bir hata
kutusu kullanıcıya sistemin bozulduğunu düşündürürdü. Ret bir cevap balonu da
değil: `.thread > .notice` tam genişlikte oturur, `.msg--ai` hizasını almaz — ve
harf harf **akmaz**, çünkü akan bir "bulamadım" sistemin cevabı düşündüğünü ima
ederdi.

## Hareket

Sayfanın **iki kurgulanmış anı**:

1. Üniteler sıraya dizilirken kademeli olarak yükselerek yerine oturur
   (`unit-in`, 520ms, `cubic-bezier(.16,1,.3,1)`, ünite başına 45ms gecikme,
   8. üniteden sonra sabit).
2. Yanıt geldiğinde künyeler (`.cites .cite`) sırayla yerleşir — galeride
   etiketlerin asılması (`label-set`, 420ms, künye başına ~60ms, 6. künyeden
   sonra sabit).

Her ikisinde de başlangıç karesi **görünür** ya da `backwards` ile bağlı:
hareket çalışmasa bile içerik okunur kalır. Hazırlık panelinde aktif adımın
halkası nefes alır (`ripple`). `prefers-reduced-motion: reduce` altında bütün
süreler 0.01ms'e iner. Bunun dışında yalnızca durum geçişleri var.

## Çizmediğimiz yüzeyler

Kaydırma çubuğu ve imleç rengi varsayılan bırakıldığında sayfa "kurulmuş" değil
"monte edilmiş" görünür. `scrollbar-color`, `caret-color` ve
`::-webkit-scrollbar-*` sistemin belirteçlerine bağlı; duvar üzerindeki
kaydırma çubuğu duvarın kendi rengiyle ölçülür.

## İkonlar

`lucide-react`, tek çizgi kalınlığı, 3.5–5 birim aralığında. Emoji ve unicode
oklar ikon yerine kullanılmaz — tek istisna karşılama duvarındaki dev ok, ki o
bir ikon değil duvarın malzemesi (`opacity: .12`, `aria-hidden`, `z-index: -1`).

## Dil

Arayüz **samimi ikinci tekil** kullanır ("Ne öğrenmek istiyorsun?", "hakkın
kaldı"). Motor terimleri kullanıcıya gösterilmez:

| Motor | Arayüz |
|---|---|
| çalıştırma / run | ders planı |
| alt konu | ünite (ve ünitenin başlığı) |
| alan / space | defter |
| parça / chunk | bölüm |
| indexed / pending / no_text / failed | Hazır / Hazırlanıyor / Metin yok — aranamaz / Eklenemedi |
| confidence 8.73/10 | (yalnızca "Neden bu video seçildi?" katında) |

Puan dökümü ve alt konu tanılaması silinmedi — katların içine alındı. Meraklı
kullanıcı bulur, yeni başlayan görmez.
