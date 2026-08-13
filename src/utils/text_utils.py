import math
import re
import unicodedata
from typing import Iterable


# NFKD ile cozulmeyen, ancak birebir Latin karsiligi olan karakterler.
# Turkce 'i' ve 'g' bunlarin basinda gelir: unicodedata bunlari ayristiramaz.
_TRANSLITERATION_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ğ": "g",
        "Ğ": "g",
        "ş": "s",
        "Ş": "s",
        "ø": "o",
        "Ø": "o",
        "æ": "ae",
        "Æ": "ae",
        "œ": "oe",
        "Œ": "oe",
        "ß": "ss",
        "ð": "d",
        "Ð": "d",
        "þ": "th",
        "Þ": "th",
        "ł": "l",
        "Ł": "l",
    }
)

# Alaka skorunda ayirt edici olmayan tokenlar. Sorgu tarafindan cikarilir ki
# "for", "ile", "nasil" gibi kelimeler ortusme oranini sulandirmasin.
_STOPWORDS = frozenset(
    {
        # Dilbilgisel
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "into",
        "is", "it", "of", "on", "or", "the", "to", "with", "what", "why", "you", "your",
        "this", "that", "using", "use", "video", "part",
        "ve", "ile", "icin", "bir", "bu", "su", "da", "de", "ki", "mi", "ne", "nasil",
        "ya", "veya", "gibi", "olarak", "uzerine", "hakkinda",
        # Pedagojik/yapisal kaliplar. Bunlar alt konu basliklarinda sik gecer ama
        # ALAN bilgisi tasimaz. Arka plan agirliklandirmasinda "ayirt edici" sayilip
        # yanlis eslesmeye yol aciyorlardi: "Zaman serisi TEMELLERI" alt konusunu
        # "Bilissel Bilimin TEMELLERI" videosu kazaniyordu. Seviye bilgisi zaten
        # `difficulty_fit` tarafindan ayrica degerlendiriliyor.
        "guide", "tutorial", "course", "lesson", "explained", "complete", "full",
        "basics", "basic", "fundamentals", "fundamental", "introduction", "intro",
        "overview", "beginners", "beginner", "advanced", "series",
        "dersi", "dersleri", "ders", "egitim", "egitimi", "kursu",
        "temel", "temeli", "temelleri", "temeller", "giris", "girisi",
        "nedir", "anlatim", "anlatimi", "ornek", "ornekler", "genel", "bakis",
        # Yapisal sifat/baglac kaliplari ("X-tabanli", "X-based"). Icerik tasimazlar
        # ama havuzda seyrek geciyorlarsa IDF onlara yuksek agirlik verir ve
        # "Agac TABANLI Modeller ve XGBoost" alt konusunu, sirf "tabanli" eslesen
        # alakasiz bir video kazanabilir.
        #
        # NOT: "model", "yontem", "mimari" gibi ALAN kelimeleri bilerek buraya
        # ALINMADI ("dil modeli" anlamli bir terim). Onlarin yaygin olmasindan
        # kaynaklanan sisme, havuz istatistigi (IDF) tarafindan cozuluyor.
        "tabanli", "dayali", "based", "driven", "oriented", "kullanarak", "araciligiyla",
    }
)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def transliterate(text: str) -> str:
    """ASCII disi harfleri en yakin Latin karsiligina cevirir.

    Onceki surum bu karakterleri siliyordu; 'Cozum Semasi' -> 'zm-emas' gibi
    bilgi kaybi yasandigi icin Turkce sorgular skorlamada kullanilamaz hale
    geliyordu.
    """
    if not text:
        return ""
    translated = text.translate(_TRANSLITERATION_MAP)
    decomposed = unicodedata.normalize("NFKD", translated)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def slugify_text(text: str) -> str:
    normalized = transliterate(normalize_text(text)).lower()
    # Kalan ASCII disi karakterleri bosluga cevir; silmek kelimeleri birlestirir.
    normalized = re.sub(r"[^a-z0-9\s-]", " ", normalized)
    normalized = re.sub(r"[\s-]+", "-", normalized)
    return normalized.strip("-")


def tokenize(text: str) -> set[str]:
    return {token for token in slugify_text(text).split("-") if token}


def query_tokens(query: str) -> set[str]:
    """Sorguyu anlamli tokenlara indirger (stopword'ler cikarilir)."""
    tokens = tokenize(query)
    meaningful = tokens - _STOPWORDS
    # Sorgu tamamen stopword'lerden olusuyorsa orijinal kumeye geri don.
    return meaningful or tokens


# Ek almis kelimeleri eslestirmek icin asgari kok uzunlugu ve izin verilen ek uzunlugu.
# Turkce eklemeli bir dil: "tahmin"/"tahmini", "model"/"modelleri", "seri"/"serisi".
# Ingilizce'de de "filter"/"filters", "estimate"/"estimation" ayni sorunu yasatir.
_MIN_STEM_LENGTH = 4
_MAX_SUFFIX_LENGTH = 4
# Kokten sonra kelimenin kendi icinde ne kadar sapabilecegi
# ("estimate"/"estimation": ortak kok "estimat", kisa olanda 1 harf artiyor).
_MAX_STEM_DIVERGENCE = 2


def _common_prefix_length(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def tokens_match(left: str, right: str) -> bool:
    """Iki token ayni kokten mi geliyor?

    Ortak on-ek uzunluguna dayali hafif bir govdeleyici. Tam bir stemmer degil,
    ama Turkce'nin eklemeli yapisini ("tahmin"/"tahmini", "model"/"modelleri") ve
    Ingilizce ekleri ("filter"/"filters", "estimate"/"estimation") yakalar.

    Yanlis pozitifler uc kisitla sinirlanir: kok en az `_MIN_STEM_LENGTH` olmali,
    uzun kelime kokten en fazla `_MAX_SUFFIX_LENGTH` kadar uzayabilir ve kisa
    kelime kokten en fazla `_MAX_STEM_DIVERGENCE` kadar sapabilir. Bu sayede
    "veri"/"verimlilikten" eslesmez.
    """
    if left == right:
        return True
    common = _common_prefix_length(left, right)
    if common < _MIN_STEM_LENGTH:
        return False
    shorter, longer = sorted((len(left), len(right)))
    if longer - common > _MAX_SUFFIX_LENGTH:
        return False
    if shorter - common > _MAX_STEM_DIVERGENCE:
        return False
    # Ayrisan kisimda RAKAM varsa ayni kok sayma. Surum numaralari teknik
    # konularda belirleyicidir: "python2"/"python3", "gpt4"/"gpt5", "http2"
    # birbirinin cekimli hali degildir.
    return not any(char.isdigit() for char in left[common:] + right[common:])


# Arka plan (konu) tokenlari, alt konuyu ayirt etmedigi icin dusuk agirlik alir.
# Yalnizca havuz istatistigi (CorpusWeights) YOKKEN kullanilan kaba yontem.
BACKGROUND_TOKEN_WEIGHT = 0.3

# Her token en azindan biraz katki versin (df cok yuksek olsa bile).
_IDF_FLOOR = 0.25


class CorpusWeights:
    """Aday havuzuna dayali token agirliklari (IDF).

    Konu/alt konu ayrimina dayali kaba agirliklandirma, ayirt edici olmayan ama
    konuda gecmeyen kelimeleri de "onemli" sayiyordu: "Agac Tabanli Modeller ve
    XGBoost" alt konusunda `xgboost` hic eslesmese bile jenerik `tabanli` ve
    `modeller` eslesmeleri videoyu one tasiyabiliyordu.

    Havuzun kendisi dogal bir korpus: `xgboost` basliklarda birkac kez, `model`
    onlarca kez gecer. Nadir token yuksek, yaygin token dusuk agirlik alir.
    """

    def __init__(self, documents: Iterable[str]):
        self.document_tokens = [tokenize(document) for document in documents]
        self.document_tokens = [tokens for tokens in self.document_tokens if tokens]
        self.document_count = len(self.document_tokens)
        self._cache: dict[str, float] = {}

    def weight(self, token: str) -> float:
        if self.document_count == 0:
            return 1.0
        cached = self._cache.get(token)
        if cached is not None:
            return cached
        # Ek almis bicimleri de ayni kok sayarak belge frekansini hesapla.
        document_frequency = sum(
            1 for tokens in self.document_tokens if any(tokens_match(token, other) for other in tokens)
        )
        weight = math.log((self.document_count + 1) / (document_frequency + 1)) + _IDF_FLOOR
        self._cache[token] = weight
        return weight


def coverage_score(
    text: str,
    query: str,
    background: str | None = None,
    weights: CorpusWeights | None = None,
) -> float:
    """Sorgu tokenlarinin kaci metinde karsilaniyor (0..1), agirlikli.

    Karsilastirma `tokens_match` ile yapilir, yani ek almis bicimler de sayilir.

    Agirlik iki kaynaktan gelebilir:
    - `weights` (TERCIH EDILEN): aday havuzundan hesaplanan gercek IDF. Nadir ve
      bu yuzden ayirt edici olan terimler ("xgboost", "arima") baskin olur.
    - `background`: havuz istatistigi yokken kullanilan kaba yontem. Konuyla
      paylasilan tokenlar dusuk agirlik alir; "LSTM ile tahmin" alt konusunda
      ayirt edici token "lstm"dir, "zaman"/"serisi" her yerde gecer.
    """
    wanted = query_tokens(query)
    if not wanted:
        return 0.0
    found = tokenize(text)
    if not found:
        return 0.0

    use_corpus = weights is not None and weights.document_count > 0
    background_tokens = set() if use_corpus else (query_tokens(background) if background else set())

    total_weight = 0.0
    matched_weight = 0.0
    for token in wanted:
        if use_corpus:
            weight = weights.weight(token)
        elif any(tokens_match(token, other) for other in background_tokens):
            weight = BACKGROUND_TOKEN_WEIGHT
        else:
            weight = 1.0
        total_weight += weight
        if any(tokens_match(token, other) for other in found):
            matched_weight += weight

    if total_weight == 0.0:
        return 0.0
    return matched_weight / total_weight


def keyword_overlap_score(text: str, query: str) -> float:
    """Geriye donuk ad; `coverage_score` ile ayni."""
    return coverage_score(text, query)
