"""Store'un SAYAC ve UPSERT yuzeyi -- IKI lehceye karsi da.

Bu suit bir uretim hatasindan dogdu. `record_api_usage` sunu yaziyordu:

    ON CONFLICT(...) DO UPDATE SET calls = calls + 1

SQLite bunu kabul ediyor. Postgres etmiyor: niteliksiz `calls`, hedef satirla
`excluded` arasinda BELIRSIZ (`AmbiguousColumn`). Hata ancak Compose yigini
ayaga kalkip bir soru sorulunca -- yani CALISMA aninda -- ortaya cikti.

Neden kacti: iki lehceli tek suit (`test_space_storage.py`) ogrenme alanlarini
kapsiyordu; `api_usage` tablosuna hic dokunmuyordu. Store'daki upsert'lerin
yalnizca birkaci iki lehcede kosuyordu.

Buradaki testler geri kalanini kapsiyor. Hepsinin ortak sinadigi sey ayni:
*ikinci kez yazmak*. Upsert hatalari ilk INSERT'te DEGIL, catisma dalinda
ortaya cikiyor -- yani bir kez yazip birakan bir test onlari goremez.
"""

from __future__ import annotations

from src.models import FilterOptions, TranscriptResult, VideoCandidate


def _candidate(video_id="vid12345678") -> VideoCandidate:
    return VideoCandidate(video_id=video_id, url="https://youtu.be/x", title="Test")


# ------------------------------------------------------------------ sayaclar


def test_api_usage_accumulates_on_the_second_call(store):
    """URETIMDE KIRILAN YOL.

    Ikinci cagri catisma dalina giriyor ve `calls = calls + 1` orada
    calisiyor. Tek cagriyla biten bir test bu hatayi HIC gormezdi.
    """
    store.record_api_usage("ali", "youtube_data_api", "search", units=100)
    store.record_api_usage("ali", "youtube_data_api", "search", units=50)

    assert store.sum_api_units(user_id="ali") == 150


def test_api_usage_is_separated_by_user(store):
    store.record_api_usage("ali", "youtube_data_api", "search", units=100)
    store.record_api_usage("veli", "youtube_data_api", "search", units=7)

    assert store.sum_api_units(user_id="ali") == 100
    assert store.sum_api_units() == 107


def test_provider_failures_accumulate_then_cool_down(store):
    """`provider_event.count` de ayni belirsizlige acikti; nitelikli yazildi."""
    assert store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3) == (1, False)
    assert store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3) == (2, False)
    assert store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3) == (3, True)

    assert store.get_provider_cooldown("prov") is not None


def test_provider_success_resets_the_counter(store):
    store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)
    store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3)

    store.clear_provider_cooldown("prov")

    assert store.record_provider_failure("prov", "err", cooldown_sec=900, threshold=3) == (1, False)


def test_marking_a_cooldown_twice_refreshes_it(store):
    store.mark_provider_cooldown("prov", "err", cooldown_sec=900)
    store.mark_provider_cooldown("prov", "baska hata", cooldown_sec=900)

    assert store.get_provider_cooldown("prov") is not None


# ------------------------------------------------------------------ onbellek


def test_rewriting_a_search_cache_entry_replaces_it(store):
    filters = FilterOptions(language="en").model_dump()
    store.put_search_cache("yt_dlp", "python", filters, [_candidate("aaaaaaaaaaa")], ttl_sec=60)
    store.put_search_cache("yt_dlp", "python", filters, [_candidate("bbbbbbbbbbb")], ttl_sec=60)

    cached = store.get_search_cache("yt_dlp", "python", filters)

    assert cached is not None
    assert [c.video_id for c in cached] == ["bbbbbbbbbbb"], "ikinci yazim ILKINI ezmeli"


def test_an_expired_search_cache_entry_is_a_miss(store):
    filters = FilterOptions(language="en").model_dump()
    store.put_search_cache("yt_dlp", "eski", filters, [_candidate()], ttl_sec=0)

    assert store.get_search_cache("yt_dlp", "eski", filters) is None


def test_rewriting_a_transcript_cache_entry_replaces_it(store):
    first = TranscriptResult(video_id="v1", status="unavailable", source="yt_dlp_subtitles")
    second = TranscriptResult(
        video_id="v1", status="available", source="yt_dlp_subtitles", text="metin" * 20
    )
    store.put_transcript_cache(first, ttl_sec=60, language="tr")
    store.put_transcript_cache(second, ttl_sec=60, language="tr")

    cached = store.get_transcript_cache("v1", "yt_dlp_subtitles", language="tr")

    assert cached is not None and cached.status == "available"


def test_the_transcript_cache_is_keyed_by_language(store):
    """Dil anahtara GIRIYOR: "Turkce yok" yaniti Ingilizce icin gecerli degil."""
    result = TranscriptResult(video_id="v1", status="unavailable", source="yt_dlp_subtitles")
    store.put_transcript_cache(result, ttl_sec=60, language="tr")

    assert store.get_transcript_cache("v1", "yt_dlp_subtitles", language="tr") is not None
    assert store.get_transcript_cache("v1", "yt_dlp_subtitles", language="en") is None


# ------------------------------------------------------------- calistirmalar


def test_creating_the_same_run_twice_is_not_an_error(store):
    store.create_run("r1", "Konu", {}, user_id="ali")
    store.create_run("r1", "Konu", {}, user_id="ali")

    assert store.get_run_summary("r1")["topic"] == "Konu"


def test_run_subtopics_and_videos_are_rewritable(store):
    store.create_run("r1", "Konu", {}, user_id="ali")

    store.add_run_subtopic("r1", 0, {"title": "Giris"})
    store.add_run_subtopic("r1", 0, {"title": "Giris (duzeltildi)"})
    store.add_run_video("r1", "transcript", "v1", {"status": "pending"})
    store.add_run_video("r1", "transcript", "v1", {"status": "available"})

    # Ayni anahtar iki kez yazildi; catisma dali patlamamali.
    assert store.get_run_summary("r1") is not None


def test_the_daily_limit_is_enforced_across_dialects(store):
    """Kabul, islemi SERILESTIREN yolun (`dialect.lock`) tek kullanicisi.

    SQLite'ta `BEGIN IMMEDIATE`, Postgres'te `pg_advisory_xact_lock`: iki
    bambaska mekanizma ve ikisinin de AYNI sonucu vermesi gerekiyor.
    """
    first = store.create_run_within_daily_limit("r1", "Konu", {}, "ali", max_per_day=2)
    second = store.create_run_within_daily_limit("r2", "Konu", {}, "ali", max_per_day=2)
    third = store.create_run_within_daily_limit("r3", "Konu", {}, "ali", max_per_day=2)

    assert first.accepted and second.accepted
    assert not third.accepted
    assert third.reason == "user_limit"


def test_the_service_budget_is_enforced_across_dialects(store):
    store.record_api_usage("baskasi", "youtube_data_api", "search", units=100)

    admission = store.create_run_within_daily_limit(
        "r1", "Konu", {}, "ali", max_per_day=0, max_units_per_day=100, estimated_units=50
    )

    assert not admission.accepted
    assert admission.reason == "service_budget"


# ------------------------------------------------------------ oturum / OAuth


def test_rewriting_a_session_token_replaces_it(store):
    store.create_session("jeton", "ali", "ali@example.com", ttl_sec=60)
    store.create_session("jeton", "veli", "veli@example.com", ttl_sec=60)

    assert store.get_session("jeton")["user_id"] == "veli"


def test_an_expired_session_is_not_returned(store):
    store.create_session("jeton", "ali", None, ttl_sec=0)

    assert store.get_session("jeton") is None


def test_oauth_state_is_single_use(store):
    store.put_oauth_state("st", "ali", "dogrulayici", ttl_sec=60, max_pending=10)

    assert store.consume_oauth_state("st") == ("ali", "dogrulayici")
    assert store.consume_oauth_state("st") is None, "ikinci kullanim tekrar oynatma olurdu"


def test_pending_oauth_states_are_capped(store):
    """Ust sinir ZORUNLU: bu ucu cagirmak oturum gerektirmiyor."""
    for index in range(5):
        store.put_oauth_state(f"st{index}", None, "v", ttl_sec=60, max_pending=3)

    assert store.count_oauth_states() <= 3


def test_saving_an_oauth_token_twice_keeps_the_latest(store):
    store.save_oauth_token("ali", "youtube", '{"access_token": "eski"}')
    store.save_oauth_token("ali", "youtube", '{"access_token": "yeni"}')

    assert store.get_oauth_token("ali", "youtube") == '{"access_token": "yeni"}'


# ------------------------------------------------------- kapsam bekcisi


def test_no_upsert_references_a_column_without_qualifying_it():
    """HATA SINIFINI dogrudan yasaklar -- bu suitin varlik sebebi.

    `DO UPDATE SET calls = calls + 1` SQLite'ta calisiyor, Postgres'te
    `AmbiguousColumn` veriyor: niteliksiz ad, hedef satirla `excluded`
    arasinda belirsiz. Uretimde tam olarak bu oldu.

    Sayi saymak yerine OZELLIGI kontrol etmek onemli: yeni bir upsert
    eklendiginde ona ayri bir iki-lehceli test yazilmasi UNUTULABILIR, ama
    bu tarama sag taraftaki her niteliksiz kendine atifi yakaliyor.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    source = (root / "src" / "storage" / "sqlite_store.py").read_text(encoding="utf-8")

    block_pattern = re.compile(r'DO UPDATE SET(.*?)(?:"""|WHERE|\n\s*",)', re.S)
    assignment_pattern = re.compile(r"(\w+)\s*=\s*([^,\n]+)")

    offenders = []
    for match in block_pattern.finditer(source):
        block = match.group(1)
        line_no = source[: match.start()].count("\n") + 1
        for assignment in assignment_pattern.finditer(block):
            target, expression = assignment.group(1), assignment.group(2).strip()
            # Sag tarafta hedef sutunun adi NITELIKSIZ geciyor mu?
            # (`api_usage.calls` ve `excluded.calls` nitelikli, sorun degil.)
            bare = re.compile(r"(?<![\w.])" + re.escape(target) + r"(?![\w.])")
            if bare.search(expression):
                offenders.append(f"sqlite_store.py:~{line_no}: {target} = {expression}")

    assert offenders == [], (
        "Upsert'te niteliksiz sutun atifi Postgres'te `AmbiguousColumn` verir; "
        "tablo adiyla nitelendirin (`tablo.sutun = tablo.sutun + 1`):\n"
        + "\n".join(offenders)
    )
