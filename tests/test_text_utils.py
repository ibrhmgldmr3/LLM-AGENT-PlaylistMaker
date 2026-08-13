import pytest

from src.utils.text_utils import keyword_overlap_score, slugify_text, transliterate


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Makine öğrenmesi", "makine-ogrenmesi"),
        ("Doğrusal Regresyon", "dogrusal-regresyon"),
        ("Çözüm Şeması", "cozum-semasi"),
        ("İstatistik", "istatistik"),
        ("Veri ön işleme", "veri-on-isleme"),
        ("Grundlagen für Anfänger", "grundlagen-fur-anfanger"),
    ],
)
def test_slugify_transliterates_instead_of_deleting(raw, expected):
    """Regresyon: 'Çözüm Şeması' -> 'zm-emas' seklinde bilgi kaybi yasaniyordu."""
    assert slugify_text(raw) == expected


def test_transliterate_keeps_word_boundaries():
    assert transliterate("öğrenme") == "ogrenme"
    assert transliterate("görenme") == "gorenme"


def test_different_turkish_words_do_not_collide():
    assert slugify_text("öğrenme") != slugify_text("görenme")


def test_turkish_query_matches_itself():
    assert keyword_overlap_score("Doğrusal Regresyon", "Doğrusal Regresyon") == 1.0


def test_turkish_overlap_is_detected_across_case_and_accents():
    assert keyword_overlap_score("DOĞRUSAL regresyon dersi", "doğrusal regresyon") == 1.0


def test_stopwords_do_not_dilute_the_query():
    """'for'/'the' gibi tokenlar sorgu kumesini sisirip skoru dusurmemeli."""
    # Sorgu tokenlari: {the, kalman, filter, for, beginners}. Dilbilgisel ("the",
    # "for") ve pedagojik ("beginners") tokenlar cikinca geriye alan terimleri
    # {kalman, filter} kalir; metin ikisini de karsiliyor.
    with_stopwords = keyword_overlap_score("kalman filter", "the kalman filter for beginners")
    assert with_stopwords == pytest.approx(1.0)
    # Stopword'ler sayilsaydi payda 5 olur ve skor 0.4'e duserdi.
    assert with_stopwords > 0.4


def test_pedagogical_words_do_not_act_as_domain_terms():
    """Regresyon: 'Bilissel Bilimin TEMELLERI' videosu, sirf 'temelleri' esletigi
    icin 'Zaman serisi temelleri' alt konusunu kazaniyordu."""
    unrelated = keyword_overlap_score("Bilişsel Bilimin Temelleri", "Zaman serisi temelleri")
    related = keyword_overlap_score("Zaman Serisi Analizine Giriş", "Zaman serisi temelleri")
    assert related > unrelated
    assert unrelated == 0.0


def test_query_made_only_of_stopwords_falls_back():
    assert keyword_overlap_score("the and for", "the and for") == 1.0


def test_non_latin_characters_do_not_merge_words():
    # Cince karakterler silinip kelimeler birlesmemeli.
    assert slugify_text("hello 世界 world") == "hello-world"
