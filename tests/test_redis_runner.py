"""`RedisJobRunner` sozlesmesi.

Her test IKI KEZ kosuyor: bir test ikizine, bir de GERCEK Redis'e karsi
(`runner` fixture'inin `params`i). Gercek sunucu yoksa ikinci kosum ATLANIR --
adres `REDIS_TEST_URL` ile verilir, ornegin:

    docker run -d -p 6399:6379 redis:7-alpine
    REDIS_TEST_URL=redis://localhost:6399/0 pytest tests/test_redis_runner.py

Ikili kosum bosuna degil: `test_worker_discards_an_envelope_whose_handle_expired`
gercek sunucuda ortaya cikan bir hatadan dogdu. Ikiz anahtar suresi dolmasini
MODELLEMIYOR, dolayisiyla o hatayi hicbir zaman gosteremezdi. Ikizin kendi
kendini dogrulamasi dogrulama sayilmaz.

Kilitlenen sozlesme `src/jobs/runner.py` icindeki `JobRunner` protokolu ve
`api/sse.py`in ondan bekledikleri:

  * olay gunlugu EKLEMELI, kuyruk degil -- iki abone de her olayi gormeli
  * `Last-Event-ID` icin indeksler KARARLI olmali
  * `is_stream_closed` bir kez True olunca akis bitmeli (SSE sonsuza donmesin)
  * iptal ISBIRLIKCI: calisan is bir sonraki `emit`te ogrenir
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from src.jobs import JobState, register_task, unregister_task
from src.jobs.redis_runner import RedisJobRunner
from src.jobs.tasks import UnknownTask
from src.jobs.worker import run_one
from src.models import ProgressEvent


# --------------------------------------------------------------- test ikizi


class _FakePubSub:
    def __init__(self, hub, ignore_subscribe_messages=True):
        self._hub = hub
        self._queue: list[str] = []
        self._channels: list[str] = []

    def subscribe(self, channel):
        self._channels.append(channel)
        self._hub.setdefault(channel, []).append(self._queue)

    def get_message(self, timeout=0.0):
        deadline = time.monotonic() + (timeout or 0.0)
        while True:
            if self._queue:
                return {"type": "message", "data": self._queue.pop(0)}
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.005)

    def close(self):
        for channel in self._channels:
            subs = self._hub.get(channel, [])
            if self._queue in subs:
                subs.remove(self._queue)


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops: list[tuple] = []

    def hset(self, key, field=None, value=None, mapping=None):
        self._ops.append(("hset", key, field, value, mapping)); return self

    def rpush(self, key, value):
        self._ops.append(("rpush", key, value)); return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl)); return self

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex)); return self

    def llen(self, key):
        self._ops.append(("llen", key)); return self

    def exists(self, key):
        self._ops.append(("exists", key)); return self

    def execute(self):
        results = []
        for op in self._ops:
            name = op[0]
            if name == "hset":
                results.append(self._redis.hset(op[1], op[2], op[3], mapping=op[4]))
            elif name == "rpush":
                results.append(self._redis.rpush(op[1], op[2]))
            elif name == "expire":
                results.append(self._redis.expire(op[1], op[2]))
            elif name == "set":
                results.append(self._redis.set(op[1], op[2], ex=op[3]))
            elif name == "llen":
                results.append(self._redis.llen(op[1]))
            elif name == "exists":
                results.append(self._redis.exists(op[1]))
        self._ops = []
        return results


class FakeRedis:
    """Kosucunun kullandigi komutlarin en kucuk uygulamasi."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self._hub: dict[str, list[list[str]]] = {}
        self._lock = threading.Lock()

    # -- hash
    def hset(self, key, field=None, value=None, mapping=None):
        with self._lock:
            bucket = self.hashes.setdefault(key, {})
            if mapping:
                bucket.update({str(k): str(v) for k, v in mapping.items()})
            if field is not None:
                bucket[str(field)] = str(value)
        return 1

    def hgetall(self, key):
        with self._lock:
            return dict(self.hashes.get(key, {}))

    # -- list
    def rpush(self, key, value):
        with self._lock:
            self.lists.setdefault(key, []).append(str(value))
            return len(self.lists[key])

    def lrange(self, key, start, end):
        with self._lock:
            items = self.lists.get(key, [])
            return items[start:] if end == -1 else items[start : end + 1]

    def llen(self, key):
        with self._lock:
            return len(self.lists.get(key, []))

    def blpop(self, keys, timeout=0):
        deadline = time.monotonic() + (timeout or 0)
        while True:
            with self._lock:
                for key in keys:
                    if self.lists.get(key):
                        return (key, self.lists[key].pop(0))
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.005)

    # -- string / genel
    def set(self, key, value, ex=None):
        with self._lock:
            self.strings[key] = str(value)
        return True

    def exists(self, key):
        with self._lock:
            return int(key in self.strings or key in self.hashes or key in self.lists)

    def expire(self, key, ttl):
        with self._lock:
            self.expires[key] = ttl
        return True

    def delete(self, *keys):
        with self._lock:
            for key in keys:
                self.hashes.pop(key, None)
                self.lists.pop(key, None)
                self.strings.pop(key, None)
        return len(keys)

    # -- pub/sub
    def publish(self, channel, message):
        with self._lock:
            for queue in self._hub.get(channel, []):
                queue.append(str(message))
        return 1

    def pubsub(self, ignore_subscribe_messages=True):
        return _FakePubSub(self._hub, ignore_subscribe_messages)

    def pipeline(self):
        return _FakePipeline(self)


# ----------------------------------------------------------------- yardimci


# Sunucunun VAR OLUP OLMADIGI bir kez olculuyor. Her test icin ayri baglanti
# denemesi, sunucusuz kosumda 22 x zaman asimi = ~45 sn ediyordu; CI bu yolu
# HER kosumda geciyor. Baglanti nesnesi degil, yalnizca KARAR onbellekleniyor.
_REAL_REDIS_UNAVAILABLE: dict[str, str] = {}


def _real_client_or_skip():
    """Gercek Redis istemcisi; sunucu yoksa testi ATLA.

    Adres `REDIS_TEST_URL` ile verilebiliyor. Sunucu yokken atlamak, CI'in
    Redis'siz de yesil kalmasini sagliyor -- ama atlanan test GECEN test gibi
    gorunmemeli, bu yuzden `-rs` ile kosuldugunda sebebi yaziliyor.
    """
    redis = pytest.importorskip("redis", reason="`redis` paketi kurulu degil")
    url = os.getenv("REDIS_TEST_URL", "redis://localhost:6379/0")
    if url in _REAL_REDIS_UNAVAILABLE:
        pytest.skip(_REAL_REDIS_UNAVAILABLE[url])

    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    try:
        client.ping()
    except Exception as exc:
        reason = f"Redis sunucusu yok ({url}): {type(exc).__name__}"
        _REAL_REDIS_UNAVAILABLE[url] = reason
        pytest.skip(reason)
    return client


@pytest.fixture(params=["fake", "real"])
def runner(request):
    """AYNI sozlesme testleri IKI uygulamaya karsi kosuyor.

    Ikizin gercek Redis'ten ayristigi her nokta burada ortaya cikar --
    ikizin kendi kendini dogrulamasi, dogrulama sayilmaz.
    """
    if request.param == "fake":
        yield RedisJobRunner(FakeRedis(), prefix="t", ttl_sec=60)
        return

    client = _real_client_or_skip()
    # Her teste OZEL onek: gercek sunucu testler arasinda durumu tasiyor.
    prefix = f"t{uuid.uuid4().hex[:10]}"
    try:
        yield RedisJobRunner(client, prefix=prefix, ttl_sec=60)
    finally:
        keys = client.keys(f"{prefix}:*")
        if keys:
            client.delete(*keys)


@pytest.fixture
def echo_task():
    """Verilen olaylari yayan basit bir is."""
    name = "_t_echo"

    def fn(context, emit):
        for value in context.payload.get("steps", []):
            emit(ProgressEvent(stage="s", message="m", progress=value))
        return "ok"

    register_task(name, fn)
    yield name
    unregister_task(name)


def _event(progress: float) -> ProgressEvent:
    return ProgressEvent(stage="s", message=f"adim {progress}", progress=progress)


# ------------------------------------------------------------------ submit


def test_submit_stores_handle_and_enqueues(runner, echo_task):
    handle = runner.submit("j1", "ali", echo_task, {"steps": [0.5]})

    assert handle.job_id == "j1"
    assert runner.get("j1").user_id == "ali"
    assert runner.get("j1").state is JobState.PENDING
    assert runner._redis.llen(runner.queue_key) == 1


def test_unknown_task_fails_immediately_and_enqueues_nothing(runner):
    """Cagiran 202 aldiktan sonra degil, HEMEN ogrenmeli."""
    with pytest.raises(UnknownTask):
        runner.submit("j1", "ali", "boyle-bir-is-yok", {})

    assert runner._redis.llen(runner.queue_key) == 0


def test_unknown_job_reads_as_missing(runner):
    assert runner.get("yok") is None


def test_result_is_refused_loudly(runner, echo_task):
    """Sessizce `None` donmek "is sonucsuz bitti" gibi gorunurdu."""
    runner.submit("j1", "ali", echo_task, {})
    with pytest.raises(NotImplementedError):
        runner.result("j1")


# -------------------------------------------------------------- olay gunlugu


def test_event_log_is_append_only_with_stable_indices(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})
    for value in (0.2, 0.4, 0.6):
        runner.append_event("j1", _event(value))

    first = runner.events_since("j1", 0)
    assert [index for index, _ in first] == [0, 1, 2]
    # Ayni imleçten TEKRAR okumak ayni sonucu vermeli -- gunluk yikici degil.
    assert [e.progress for _, e in runner.events_since("j1", 0)] == [0.2, 0.4, 0.6]
    assert [e.progress for _, e in runner.events_since("j1", 2)] == [0.6]


def test_two_subscribers_each_see_every_event(runner, echo_task):
    """Kuyruk olsaydi olaylar aboneler arasinda BOLUNURDU."""
    runner.submit("j1", "ali", echo_task, {})
    for value in (0.5, 1.0):
        runner.append_event("j1", _event(value))

    a = [e.progress for _, e in runner.events_since("j1", 0)]
    b = [e.progress for _, e in runner.events_since("j1", 0)]

    assert a == b == [0.5, 1.0]


def test_corrupt_event_does_not_stall_the_cursor(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})
    runner.append_event("j1", _event(0.5))
    runner._redis.rpush(runner._events_key("j1"), "bu JSON degil")
    runner.append_event("j1", _event(1.0))

    events = runner.events_since("j1", 0)

    # Bozuk satir yer tutucuyla doner; SONRAKI olay yine okunuyor.
    assert [e.progress for _, e in events] == [0.5, 0.5, 1.0]
    assert [i for i, _ in events] == [0, 1, 2]


def test_trailing_corrupt_event_still_advances_the_cursor(runner, echo_task):
    """SONDAKI bozuk olay imleci ilerletmeli.

    Atlandiginda imlec sabit kaliyor ama `_has_news` listede okunmamis eleman
    gorup ANINDA True donuyor: `wait_for_events` hic beklemiyor ve `api/sse.py`
    ureticisi is bitene kadar bos donen sikisik bir donguye giriyor.
    """
    runner.submit("j1", "ali", echo_task, {})
    runner.append_event("j1", _event(0.5))
    runner._redis.rpush(runner._events_key("j1"), "bu JSON degil")

    cursor = 0
    for index, _event_obj in runner.events_since("j1", cursor):
        cursor = index + 1

    assert cursor == 2, "imlec bozuk olayin OTESINE gecmeli"
    # Asil sozlesme: okunacak yeni bir sey kalmadiginda bekleme GERCEKTEN beklemeli.
    assert runner._has_news("j1", cursor) is False
    started = time.monotonic()
    assert runner.wait_for_events("j1", cursor, 0.2) is False
    assert time.monotonic() - started >= 0.15, "bekleme aninda donduyse dongu sikisir"


def test_corrupt_event_does_not_rewind_progress(runner, echo_task):
    """Yer tutucu ilerlemeyi GERIYE sicratmamali."""
    runner.submit("j1", "ali", echo_task, {})
    runner.append_event("j1", _event(0.8))
    runner._redis.rpush(runner._events_key("j1"), "bu JSON degil")

    progresses = [e.progress for _, e in runner.events_since("j1", 0)]

    assert progresses == [0.8, 0.8], "ilerleme cubugu bastan baslamis gibi gorunmemeli"


# ------------------------------------------------------------------ kapanis


def test_stream_closes_only_after_finish(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})
    assert runner.is_stream_closed("j1") is False

    runner.finish("j1", JobState.DONE)

    assert runner.is_stream_closed("j1") is True
    assert runner.get("j1").state is JobState.DONE


def test_unknown_job_counts_as_closed(runner):
    """Silinmis/suresi dolmus is icin akis SONSUZA KADAR beklememeli."""
    assert runner.is_stream_closed("hic-olmayan") is True


# --------------------------------------------------------------------- bekleme


def test_wait_returns_true_when_an_event_is_already_there(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})
    runner.append_event("j1", _event(0.5))

    assert runner.wait_for_events("j1", 0, timeout=0.2) is True


def test_wait_times_out_without_news(runner, echo_task):
    """Zaman asimi `False` donmeli: SSE bunu heartbeat'e ceviriyor."""
    runner.submit("j1", "ali", echo_task, {})

    assert runner.wait_for_events("j1", 0, timeout=0.15) is False


def test_wait_wakes_up_on_a_new_event(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})

    def emit_later():
        time.sleep(0.05)
        runner.append_event("j1", _event(0.5))

    threading.Thread(target=emit_later, daemon=True).start()

    assert runner.wait_for_events("j1", 0, timeout=2.0) is True


def test_wait_returns_true_when_the_stream_closes(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})

    def close_later():
        time.sleep(0.05)
        runner.finish("j1", JobState.DONE)

    threading.Thread(target=close_later, daemon=True).start()

    assert runner.wait_for_events("j1", 0, timeout=2.0) is True


# ----------------------------------------------------------------------- iptal


def test_cancel_marks_a_pending_job_and_closes_the_stream(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})

    assert runner.cancel("j1") is True
    assert runner.get("j1").state is JobState.CANCELLED
    # Akis kapanmali, yoksa dinleyiciler takilir.
    assert runner.is_stream_closed("j1") is True


def test_cancel_is_false_for_a_finished_job(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {})
    runner.finish("j1", JobState.DONE)

    assert runner.cancel("j1") is False


def test_cancel_is_false_for_an_unknown_job(runner):
    assert runner.cancel("yok") is False


# ---------------------------------------------------------------------- worker


def test_worker_runs_a_task_and_marks_it_done(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {"steps": [0.5, 1.0]})
    envelope = runner.claim(timeout=1)

    run_one(runner, envelope)

    assert runner.get("j1").state is JobState.DONE
    assert [e.progress for _, e in runner.events_since("j1", 0)] == [0.5, 1.0]
    assert runner.is_stream_closed("j1") is True


def test_worker_stops_a_cancelled_job_at_the_next_emit(runner):
    """Iptal ISBIRLIKCI: is bir sonraki ilerleme bildiriminde ogreniyor."""
    name = "_t_long"
    seen: list[float] = []

    def fn(context, emit):
        for index in range(50):
            seen.append(index)
            emit(ProgressEvent(stage="s", message="m", progress=index / 50))

    register_task(name, fn)
    try:
        runner.submit("j1", "ali", name, {})
        envelope = runner.claim(timeout=1)
        runner._redis.set(runner._cancel_key("j1"), "1")

        run_one(runner, envelope)
    finally:
        unregister_task(name)

    assert runner.get("j1").state is JobState.CANCELLED
    assert len(seen) < 50, "is iptali hic gormedi"


def test_worker_skips_a_job_cancelled_while_queued(runner, echo_task):
    runner.submit("j1", "ali", echo_task, {"steps": [0.5]})
    envelope = runner.claim(timeout=1)
    runner._redis.set(runner._cancel_key("j1"), "1")

    run_one(runner, envelope)

    assert runner.get("j1").state is JobState.CANCELLED
    assert runner.events_since("j1", 0) == [], "hic baslamamaliydi"


def test_worker_records_a_failure_with_a_redacted_message(runner):
    name = "_t_boom"

    # SAHTE anahtar IKI esigin ARASINDA duruyor ve durmasi gerekiyor:
    #   `AIza` + 10 karakter  -> maskeleme devreye girer (`logging_utils`)
    #   `AIza` + 30 karakter  -> CI'in gizli bilgi taramasi ELER
    # Gercek bir Gemini anahtari 39 karakter, yani ikinci esigin ustunde.
    # Fixture'i uzun yazmak testi guclendirmiyor, yalnizca tarayiciya gercek
    # anahtar gibi gorunuyor -- bir kez yasandi ve CI'i kirdi. Suitin geri
    # kalani da ayni sabiti kullaniyor.
    def fn(context, emit):
        raise RuntimeError("patladi key=AIzaSyTOPSECRETVALUE123")

    register_task(name, fn)
    try:
        runner.submit("j1", "ali", name, {})
        run_one(runner, runner.claim(timeout=1))
    finally:
        unregister_task(name)

    handle = runner.get("j1")
    assert handle.state is JobState.FAILED
    assert "patladi" in handle.error
    assert "AIzaSy" not in handle.error, "anahtar istemciye gidebilirdi"
    assert runner.is_stream_closed("j1") is True


def test_worker_fails_an_unknown_task_instead_of_requeueing(runner):
    """Ayni zarf sonsuza kadar dolasip her worker'da patlamamali."""
    runner._redis.rpush(
        runner.queue_key,
        '{"job_id": "j1", "user_id": "ali", "task": "yok-boyle", "payload": {}}',
    )
    runner._redis.hset(runner._job_key("j1"), mapping={"job_id": "j1", "user_id": "ali", "state": "pending"})

    run_one(runner, runner.claim(timeout=1))

    assert runner.get("j1").state is JobState.FAILED
    assert runner._redis.llen(runner.queue_key) == 0


def test_worker_discards_an_envelope_whose_handle_expired(runner, echo_task):
    """TTL'i dolmus bir zarf CALISTIRILMAMALI.

    Gercek Redis'e karsi kosarken bulundu; test ikizi anahtar suresi dolmasini
    modellemedigi icin bunu gosteremezdi.

    Isi bekleyen kimse kalmadi (istemci coktan "bilinmeyen calistirma" gordu)
    ama is yine de YouTube kotasi ve LLM cagrisi harcardi. Ustelik
    `mark_running`in `hset`i hash'i YENIDEN YARATIP `user_id`si bos bir zombi
    tutamac birakiyordu -- hicbir kullaniciyla eslesmedigi icin ne gorulebilen
    ne iptal edilebilen bir kayit.
    """
    runner.submit("j1", "ali", echo_task, {"steps": [0.5]})
    envelope = runner.claim(timeout=1)

    # TTL doldu: tutamac anahtari yok oldu.
    runner._redis.delete(runner._job_key("j1"))

    run_one(runner, envelope)

    assert runner.get("j1") is None, "zombi tutamac olusmamali"
    assert runner.events_since("j1", 0) == [], "is hic calismamaliydi"
