"""Siralama KALITESI testleri: dogru videonun kazandigini dogrular."""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import FilterOptions, MetadataScore, VideoCandidate
from src.services.metadata_ranker import rank_candidates, score_candidate, to_confidence
from src.services.recommendation_service import assign_recommendations, select_recommendation
from src.utils.text_utils import CorpusWeights, coverage_score, tokens_match


TOPIC = "Makine öğrenmesi ile zaman serisi tahmini"


def _video(video_id, title, **kwargs):
    defaults = dict(
        url=f"https://youtu.be/{video_id}",
        description="",
        duration_sec=1200,
        language="tr",
    )
    defaults.update(kwargs)
    return VideoCandidate(video_id=video_id, title=title, **defaults)


def _filters(**kwargs):
    values = dict(language="tr", max_duration_minutes=60, freshness_preference="balanced")
    values.update(kwargs)
    return FilterOptions(**values)


# --------------------------------------------------------------------- #
# 1. Alt konu sinyali konu sinyalinin altinda kaybolmamali
# --------------------------------------------------------------------- #

SUBTOPICS = ["Zaman serisi temelleri", "ARIMA modeli", "LSTM ile tahmin"]


def _example_pool():
    return [
        _video("intro", "Zaman Serisi Analizine Giriş"),
        _video("arima", "ARIMA Modeli ile Satış Tahmini"),
        _video("lstm", "LSTM Derin Öğrenme ile Zaman Serisi"),
    ]


def test_subtopic_drives_ranking_not_just_the_shared_topic():
    """Regresyon: sorgu 'konu + alt konu' olarak birlestirildiginde ortak konu
    tokenlari baskin geliyor ve HER alt konu ayni videoyu kazandiriyordu."""
    winners = {
        subtopic: rank_candidates(_example_pool(), TOPIC, subtopic, _filters())[0][0].video_id
        for subtopic in SUBTOPICS
    }

    # Alt konusunu adiyla iceren videolar artik net sekilde one cikiyor.
    assert winners["ARIMA modeli"] == "arima"
    assert winners["LSTM ile tahmin"] == "lstm"
    # Eskiden ucu de ayni videoyu kazandiriyordu.
    assert len(set(winners.values())) >= 2, f"alt konular ayrisamadi: {winners}"


def test_global_assignment_sends_each_video_to_its_best_subtopic():
    """Alt konu sirasina gore acgozlu secimin cozemedigi durum.

    "Zaman serisi temelleri" ile "LSTM ile tahmin" alt konularinin ikisi de LSTM
    videosuna talip; ama LSTM videosu ikinci alt konuya cok daha iyi uyuyor.
    Acgozlu secim sirayla gittigi icin videoyu ILK alt konuya veriyordu.
    """
    pools = [
        (subtopic, rank_candidates(_example_pool(), TOPIC, subtopic, _filters()))
        for subtopic in SUBTOPICS
    ]

    assignments = assign_recommendations(pools, {})
    chosen = {SUBTOPICS[i]: rec.video.video_id for i, rec in enumerate(assignments) if rec}

    assert chosen["LSTM ile tahmin"] == "lstm"
    assert chosen["ARIMA modeli"] == "arima"
    assert chosen["Zaman serisi temelleri"] == "intro"


def test_assignment_positions_follow_subtopic_order():
    pools = [
        (subtopic, rank_candidates(_example_pool(), TOPIC, subtopic, _filters()))
        for subtopic in SUBTOPICS
    ]
    assignments = assign_recommendations(pools, {})
    assert [rec.position for rec in assignments if rec] == [1, 2, 3]


def test_assignment_never_reuses_a_video():
    pools = [
        (subtopic, rank_candidates(_example_pool(), TOPIC, subtopic, _filters()))
        for subtopic in SUBTOPICS
    ]
    ids = [rec.video.video_id for rec in assign_recommendations(pools, {}) if rec]
    assert len(ids) == len(set(ids))


def test_assignment_maximises_total_fit_not_the_single_best_pair():
    """Acgozlu secimin yapisal zaafi.

    A alt konusu iki videoya da 5.0 veriyor; B alt konusu yalnizca 'shared'
    videosuna 4.9, digerine 0.0 veriyor. Acgozlu once (A, shared)=5.0 cifti alir
    ve B'yi bos birakir (toplam 5.0). Dogru cozum A'ya 'solo', B'ye 'shared'
    vermek (toplam 9.9).
    """
    shared = _video("shared", "Ortak")
    solo = _video("solo", "Tekil")
    pools = [
        ("A", [(shared, _score(5.0)), (solo, _score(5.0))]),
        ("B", [(shared, _score(4.9)), (solo, _score(0.0))]),
    ]

    assignments = assign_recommendations(pools, {})

    assert assignments[0].video.video_id == "solo"
    assert assignments[1].video.video_id == "shared"


def test_recommendation_carries_its_own_subtopic_score():
    """Regresyon: havuzlama sonrasi her alt konu kendi kopyasini uretiyor; ortak
    katalogdan alinan aday, videoyu ILK siralayan alt konunun puanini tasiyordu.
    Sonuc: dis aktarilan JSON'da video.metadata_score ile metadata_score.total
    birbirini tutmuyordu."""
    pools = [
        (subtopic, rank_candidates(_example_pool(), TOPIC, subtopic, _filters()))
        for subtopic in SUBTOPICS
    ]

    for recommendation in assign_recommendations(pools, {}):
        if recommendation is None:
            continue
        assert recommendation.video.metadata_score == pytest.approx(
            recommendation.metadata_score.total
        ), f"{recommendation.subtopic}: aday puani atandigi alt konuya ait degil"


def test_assignment_is_deterministic():
    pools = [
        (subtopic, rank_candidates(_example_pool(), TOPIC, subtopic, _filters()))
        for subtopic in SUBTOPICS
    ]
    first = [rec.video.video_id for rec in assign_recommendations(pools, {}) if rec]
    second = [rec.video.video_id for rec in assign_recommendations(pools, {}) if rec]
    assert first == second


def test_assignment_leaves_subtopic_empty_when_pool_is_exhausted():
    single = [_video("only", "Tek video")]
    pools = [(subtopic, rank_candidates(list(single), TOPIC, subtopic, _filters())) for subtopic in SUBTOPICS]

    assignments = assign_recommendations(pools, {})

    assert sum(1 for rec in assignments if rec) == 1


def test_offtopic_video_still_loses():
    pool = [_video("ontopic", "ARIMA Modeli Anlatımı"), _video("offtopic", "Photoshop ile Poster Tasarımı")]
    ranked = rank_candidates(pool, TOPIC, "ARIMA modeli", _filters())
    assert ranked[0][0].video_id == "ontopic"


def test_subtopic_match_beats_topic_match_on_title():
    """Alt konuyu karsilayan baslik, sadece konuyu karsilayandan yuksek almali."""
    subtopic_hit = score_candidate(_video("a", "ARIMA modeli"), TOPIC, _filters(), subtopic="ARIMA modeli")
    topic_hit = score_candidate(_video("b", "Makine öğrenmesi zaman serisi"), TOPIC, _filters(), subtopic="ARIMA modeli")
    assert subtopic_hit.title_relevance > topic_hit.title_relevance


# --------------------------------------------------------------------- #
# 2. Ek almis kelimeler eslesmeli
# --------------------------------------------------------------------- #

def test_turkish_suffixes_match_the_same_stem():
    """Regresyon: 'tahmin' ile 'tahmini' eslesmiyordu."""
    assert tokens_match("tahmin", "tahmini")
    assert tokens_match("model", "modelleri")
    assert tokens_match("seri", "serisi")
    assert tokens_match("veri", "verileri")


def test_english_suffixes_match_the_same_stem():
    assert tokens_match("filter", "filters")
    assert tokens_match("estimate", "estimation")


def test_version_numbers_are_not_treated_as_inflections():
    """Regresyon: 'python2'/'python3' ayni kok sayiliyordu.

    Teknik ogrenme konularinda surum numarasi ayirt edicidir; bunlari
    birlestirmek yanlis videoyu one cikarir.
    """
    assert not tokens_match("python2", "python3")
    assert not tokens_match("gpt4", "gpt5")
    assert not tokens_match("http2", "http3")
    assert not tokens_match("good1", "good2")
    # Rakam icermeyen normal cekimler etkilenmemeli.
    assert tokens_match("tahmin", "tahmini")
    assert tokens_match("model", "modelleri")


def test_unrelated_words_do_not_match():
    assert not tokens_match("ogrenme", "gorenme")
    assert not tokens_match("kedi", "kopek")
    # Cok kisa kokler on-ek kuralindan muaf: yanlis pozitifi onler.
    assert not tokens_match("al", "alaka")
    # Ek cok uzunsa ayni kokten sayilmaz.
    assert not tokens_match("veri", "verimlilikten")


def test_coverage_uses_stem_matching():
    assert coverage_score("ARIMA modeli ile satış tahmini", "ARIMA model tahmin") == 1.0


# --------------------------------------------------------------------- #
# 2b. Havuz istatistigi (IDF): nadir terim agirligi tasimali
# --------------------------------------------------------------------- #

_POOL = [
    "Karakter Seviyesinde LSTM Tabanlı Dil Modeli ile Otomatik Metin Üretimi",
    "Time Series Forecasting with XGBoost - Use python and machine learning",
    "Derin öğrenme algoritmaları ile zaman serisi tahmin modelleri",
    "ZAMAN SERİLERİ ANALİZİ | ÖNEMLİ METOTLAR | VERİ BİLİMİ",
    "10 - Keras LSTM ile Zaman Serisi Tahmini",
    "Zaman Serisi Analizine Giriş | Veri Bilimi",
    "Python ile Zaman Serisi Modelleme Dersleri",
    "LSTM Time Series Forecasting with TensorFlow & Python",
    "Anlaşılır Ekonomi Python ile Zaman Serisi (ARIMA Örnek-1)",
    "Random Forest ve Karar Ağaçları Modeli Anlatımı",
]


def test_rare_terms_outweigh_common_ones():
    weights = CorpusWeights(_POOL)
    # "xgboost" havuzda 1 kez, "zaman"/"serisi" onlarca kez geciyor.
    assert weights.weight("xgboost") > weights.weight("zaman") > weights.weight("serisi")


def test_distinctive_term_decides_the_match():
    """Regresyon: ayirt edici terim ('xgboost') eslesmese bile jenerik
    eslesmeler ('tabanli', 'modeller') videoyu one tasiyabiliyordu."""
    weights = CorpusWeights(_POOL)
    subtopic = "Ağaç Tabanlı Modeller ve XGBoost"
    wrong = "Karakter Seviyesinde LSTM Tabanlı Dil Modeli ile Otomatik Metin Üretimi"
    right = "Time Series Forecasting with XGBoost - Use python and machine learning"

    assert coverage_score(right, subtopic, weights=weights) > coverage_score(wrong, subtopic, weights=weights)


def test_ranking_prefers_the_tool_named_in_the_subtopic():
    candidates = [
        _video(f"v{i}", title, view_count=20_000, subscriber_count=50_000)
        for i, title in enumerate(_POOL)
    ]
    ranked = rank_candidates(candidates, TOPIC, "Ağaç Tabanlı Modeller ve XGBoost", _filters())
    top_titles = [candidate.title for candidate, _score in ranked[:2]]
    assert any("XGBoost" in title for title in top_titles)
    assert not any("Otomatik Metin" in title for title in top_titles)


def test_empty_corpus_falls_back_to_background_weighting():
    """Havuz yoksa (birim testler, tek aday) eski yontem devrede kalmali."""
    weights = CorpusWeights([])
    assert weights.document_count == 0
    score = coverage_score("ARIMA modeli", "ARIMA modeli", weights=weights)
    assert score == 1.0


def test_structural_adjectives_contribute_nothing():
    """'tabanli'/'based' havuzda seyrek gecse bile ayirt edici sayilmamali.

    Dogru olcut mutlak bir esik degil, 'tabanli'nin skoru HIC degistirmemesi:
    sayilsaydi eslesme 2/3'e cikip alakasiz videoyu one tasirdi.
    """
    with_adjective = coverage_score("LSTM Tabanlı Dil Modeli", "Ağaç Tabanlı Modeller")
    without_adjective = coverage_score("LSTM Dil Modeli", "Ağaç Modeller")
    assert with_adjective == without_adjective

    # Ayirt edici terim eslesirse skor belirgin sekilde yukselmeli.
    distinctive = coverage_score("Ağaç Tabanlı Modeller XGBoost", "Ağaç Tabanlı Modeller")
    assert distinctive > with_adjective


# --------------------------------------------------------------------- #
# 3. Kanal otoritesi gercek veriye dayanmali
# --------------------------------------------------------------------- #

def test_subscriber_count_beats_keyword_in_channel_name():
    """Regresyon: adinda 'tutorial' gecen kanal, MIT'ten yuksek puan aliyordu."""
    keyword_channel = _video("a", "Zaman serisi", channel="Random Tutorial Guy")
    real_authority = _video("b", "Zaman serisi", channel="MIT OpenCourseWare", subscriber_count=5_000_000)

    weak = score_candidate(keyword_channel, TOPIC, _filters(), subtopic="temeller")
    strong = score_candidate(real_authority, TOPIC, _filters(), subtopic="temeller")

    assert strong.channel_quality > weak.channel_quality


def test_channel_authority_scales_with_subscribers():
    def quality(subs):
        return score_candidate(
            _video("x", "Zaman serisi", subscriber_count=subs), TOPIC, _filters(), subtopic="temeller"
        ).channel_quality

    assert quality(1_000) < quality(50_000) < quality(1_000_000) < quality(10_000_000)


def test_unknown_subscribers_falls_back_without_crashing():
    score = score_candidate(
        _video("x", "Zaman serisi", channel="Caner Erden"), TOPIC, _filters(), subtopic="temeller"
    )
    assert 0.0 <= score.channel_quality <= 2.2


def test_view_count_is_not_double_counted_in_channel_quality():
    """Regresyon: izlenme hem `channel_quality`'de hem `engagement`'ta sayiliyordu."""
    low = score_candidate(_video("a", "T", view_count=100), TOPIC, _filters(), subtopic="s")
    high = score_candidate(_video("b", "T", view_count=5_000_000), TOPIC, _filters(), subtopic="s")
    assert low.channel_quality == high.channel_quality


# --------------------------------------------------------------------- #
# 4. Etkilesim yasa gore normalize edilmeli
# --------------------------------------------------------------------- #

def _iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_recent_video_beats_old_video_at_equal_view_count():
    """Regresyon: 5 yillik 100k izlenme ile 2 haftalik 100k ayni puani aliyordu."""
    fresh = score_candidate(
        _video("a", "T", view_count=100_000, publish_date=_iso_days_ago(14)),
        TOPIC, _filters(), subtopic="s",
    )
    stale = score_candidate(
        _video("b", "T", view_count=100_000, publish_date=_iso_days_ago(5 * 365)),
        TOPIC, _filters(), subtopic="s",
    )
    assert fresh.engagement > stale.engagement


def test_engagement_stays_within_bounds():
    huge = score_candidate(
        _video("a", "T", view_count=900_000_000, publish_date=_iso_days_ago(3)),
        TOPIC, _filters(), subtopic="s",
    )
    assert 0.0 <= huge.engagement <= 1.0


# --------------------------------------------------------------------- #
# 5. Guven puani ayirt edici olmali
# --------------------------------------------------------------------- #

def test_confidence_spreads_across_the_realistic_band():
    """Regresyon: gercek videolar 4.7-5.8 arasina sikisiyordu."""
    weak, mid, strong = to_confidence(2.0), to_confidence(5.5), to_confidence(9.0)
    assert 0.0 < weak < mid < strong <= 10.0
    # Tipik iyi/kotu aday arasinda en az 4 puanlik fark olmali.
    assert strong - weak >= 4.0


def test_confidence_is_clamped():
    assert to_confidence(99.0) == 10.0
    assert to_confidence(-99.0) == 0.0


# --------------------------------------------------------------------- #
# 6. Playlist cesitliligi
# --------------------------------------------------------------------- #

def _score(total):
    return MetadataScore(
        total=total, title_relevance=1.0, description_relevance=0.5, channel_quality=0.5,
        duration_fit=1.0, difficulty_fit=0.25, language_match=1.0, freshness=0.5,
        engagement=0.25, rationale=[],
    )


def test_channel_repeat_penalty_breaks_near_ties():
    same_channel = _video("v2", "B", channel_id="chan-A")
    other_channel = _video("v3", "C", channel_id="chan-B")
    ranked = [(same_channel, _score(5.0)), (other_channel, _score(4.8))]

    recommendation = select_recommendation(
        "konu", ranked, {}, set(), 1, used_channel_ids={"chan-A"}, channel_repeat_penalty=0.6
    )

    assert recommendation.video.video_id == "v3"


def test_channel_repeat_penalty_does_not_override_a_clearly_better_video():
    same_channel = _video("v2", "B", channel_id="chan-A")
    other_channel = _video("v3", "C", channel_id="chan-B")
    ranked = [(same_channel, _score(8.0)), (other_channel, _score(4.0))]

    recommendation = select_recommendation(
        "konu", ranked, {}, set(), 1, used_channel_ids={"chan-A"}, channel_repeat_penalty=0.6
    )

    assert recommendation.video.video_id == "v2"


def test_selected_channel_is_recorded():
    used_channels: set[str] = set()
    ranked = [(_video("v1", "A", channel_id="chan-X"), _score(5.0))]

    select_recommendation("konu", ranked, {}, set(), 1, used_channel_ids=used_channels, channel_repeat_penalty=0.6)

    assert used_channels == {"chan-X"}


def test_popular_but_offtopic_video_cannot_win_on_popularity_alone():
    """Regresyon: populerlik sinyalleri alaka sinyallerinden AGIR basiyordu.

    Alaka disi sinyallerin tavani (kanal 2.2 + sure 2.0 + dil 1.5 + tazelik 1.5
    + etkilesim 1.0 = 8.2) alaka sinyallerininkinden (baslik 3.2 + aciklama 1.6
    = 4.8) yuksekti. Sonuc: konuyla hic eslesmeyen ama populer bir video slotu
    kazanabiliyordu.

    Kayitli kosularda olculdu: 42 secimin 10'u leksik sinyali olmadan yapilmisti
    ve TEK bir viral video uc ayri alt basligi birden kapmisti. Populerlik
    sinyalleri artik alakaya bagli aciliyor.
    """
    subtopic = "ARIMA modeli ile tahmin"
    fresh = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()

    # Konudan hicbir ayirt edici kelime tasimiyor ama her populerlik sinyali tavanda.
    viral = _video(
        "viral123456",
        "PARA KAZANMANIN 10 YOLU",
        subscriber_count=5_000_000,
        view_count=8_000_000,
        publish_date=fresh,
    )
    # Alt basligi KISMEN karsiliyor -- gercek kirilmalar burada oluyor. Tam
    # eslesen bir baslik (alaka ~3.7) kapi olmadan da kazaniyor, dolayisiyla
    # onunla yazilan bir test hicbir seyi korumaz. Bu baslik 1.39 aliyor;
    # olculen basarisiz secimler de bu bantta (0.12-0.43 kazananla).
    relevant = _video(
        "arima1234567",
        "Ekonometri dersleri 12: ARIMA",
        subscriber_count=900,
        view_count=1_200,
        publish_date=(datetime.now(timezone.utc) - timedelta(days=900)).isoformat(),
    )

    ranked = rank_candidates([viral, relevant], TOPIC, subtopic, _filters())

    assert ranked[0][0].video_id == "arima1234567", (
        "alt basligi karsilayan video, yalnizca populer olana yenilmemeli: "
        f"{[(c.video_id, round(s.total, 2)) for c, s in ranked]}"
    )


def test_popularity_still_decides_between_equally_relevant_videos():
    """Kapi populerligi YOK ETMIYOR, alakaya BAGLIYOR.

    Iki video da alt basligi karsiladiginda kanal otoritesi/tazelik yine
    ayirt edici olmali; aksi halde duzeltme, kaliteli kanallari one cikaran
    davranisi da birlikte goturmus olurdu.
    """
    subtopic = "ARIMA modeli ile tahmin"
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    big = _video(
        "arimabig1234",
        "ARIMA modeli ile zaman serisi tahmini",
        subscriber_count=400_000,
        view_count=250_000,
        publish_date=recent,
    )
    small = _video(
        "arimasml1234",
        "ARIMA modeli ile zaman serisi tahmini",
        subscriber_count=300,
        view_count=400,
        publish_date=recent,
    )

    ranked = rank_candidates([small, big], TOPIC, subtopic, _filters())

    assert ranked[0][0].video_id == "arimabig1234"
