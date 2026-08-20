"""Silme gercekten silmeli: veritabani VE disk.

Bir calistirma iki yerde yasiyor. Silme uzun sure yalnizca veritabanina
dokunuyordu; kullanici gecmisten "Sil"e basiyor, kayit listeden kayboluyor ama
urettigi calisma plani ve sonuc JSON'u diskte kaliyordu.
"""

from __future__ import annotations

from src.config import AppConfig
from src.services.run_retention import delete_run, purge_orphan_run_dirs
from src.storage import SQLiteStore


def _setup(tmp_path):
    config = AppConfig(
        gemini_api_key="k",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "app.db"),
    )
    config.ensure_directories()
    store = SQLiteStore(config.sqlite_path, encryption_key=None)
    return config, store


def _make_run(config, store, run_id: str, user_id: str = "ali"):
    store.create_run(run_id, "konu", {}, user_id=user_id)
    run_dir = config.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "study_plan.md").write_text("kullanicinin calisma plani", encoding="utf-8")
    (run_dir / "result.json").write_text("{}", encoding="utf-8")
    return run_dir


def test_deleting_a_run_removes_its_files_too(tmp_path):
    config, store = _setup(tmp_path)
    run_dir = _make_run(config, store, "r1")

    assert delete_run(config, store, "r1", user_id="ali") is True

    assert not run_dir.exists(), "kullanicinin icerigi diskte kalmamali"
    assert store.get_run_summary("r1") is None


def test_someone_elses_run_is_not_deleted_from_disk_either(tmp_path):
    """Yetki kontrolu veritabani sorgusunun icinde; satir silinmezse diske
    HIC dokunulmamali."""
    config, store = _setup(tmp_path)
    run_dir = _make_run(config, store, "r1", user_id="ali")

    assert delete_run(config, store, "r1", user_id="veli") is False

    assert run_dir.exists(), "baskasinin dosyalarina dokunulmamaliydi"
    assert store.get_run_summary("r1") is not None


def test_deleting_a_run_without_a_directory_still_succeeds(tmp_path):
    """Dizin hic olusmamis olabilir (calistirma kabul edildi ama hic baslamadi)."""
    config, store = _setup(tmp_path)
    store.create_run("r1", "konu", {}, user_id="ali")

    assert delete_run(config, store, "r1", user_id="ali") is True


def test_orphan_directories_are_collected(tmp_path):
    """Gecmiste birikmis, veritabaninda karsiligi olmayan dizinler."""
    config, store = _setup(tmp_path)
    kalan = _make_run(config, store, "kayitli")

    sahipsiz = config.runs_dir / "sahipsiz"
    sahipsiz.mkdir(parents=True)
    (sahipsiz / "run.log").write_text("eski", encoding="utf-8")

    assert purge_orphan_run_dirs(config, store) == 1

    assert not sahipsiz.exists()
    assert kalan.exists(), "veritabaninda kayitli olan dizin silinmemeli"


def test_a_suspicious_run_id_never_escapes_the_runs_directory(tmp_path):
    """`run_id` bir yol parcasi olarak kullaniliyor; alfanumerik degilse dokunma."""
    config, store = _setup(tmp_path)
    komsu = config.data_dir_path.parent if hasattr(config, "data_dir_path") else tmp_path
    hedef = config.runs_dir.parent / "dokunulmaz"
    hedef.mkdir(parents=True, exist_ok=True)
    (hedef / "onemli.txt").write_text("silinmemeli", encoding="utf-8")

    # Veritabaninda boyle bir satir olmadigi icin zaten False donmeli;
    # asil olcut: dosya sisteminde hicbir sey kaybolmamis olmali.
    assert delete_run(config, store, "../dokunulmaz", user_id="ali") is False
    assert (hedef / "onemli.txt").exists()


# ------------------------------------------- yarida kalan calistirma ve kota

def test_an_interrupted_run_gives_the_daily_slot_back(tmp_path):
    """Sunucu yeniden basladi diye kullanici hakkini KAYBETMEMELI.

    Satir kabul aninda yaziliyor (TOCTOU duzeltmesi bunu gerektiriyor), yani
    sonucun ne olacagi o an bilinmiyor. Yarida kalan calistirmalar acilista
    isaretlenip sayimdan dusuluyor.
    """
    _, store = _setup(tmp_path)

    assert store.create_run_within_daily_limit("r1", "k", {}, user_id="ali", max_per_day=1)
    assert not store.create_run_within_daily_limit("r2", "k", {}, user_id="ali", max_per_day=1)

    # Sunucu yeniden basladi: r1 sonuc uretmeden yarida kaldi.
    assert store.mark_interrupted_runs() == 1

    assert store.create_run_within_daily_limit("r3", "k", {}, user_id="ali", max_per_day=1), (
        "yarida kalan calistirma hakki geri vermeliydi"
    )


def test_a_completed_run_still_counts(tmp_path):
    """Iade YALNIZCA yarida kalanlara; tamamlanan calistirma hakki yakmali."""
    from src.models import FilterOptions, PlaylistResult

    _, store = _setup(tmp_path)
    store.create_run_within_daily_limit("r1", "k", {}, user_id="ali", max_per_day=1)
    store.finalize_run(
        "r1",
        PlaylistResult(
            run_id="r1", topic="k", filters=FilterOptions(), subtopics=[], recommendations=[]
        ),
    )

    assert store.mark_interrupted_runs() == 0
    assert not store.create_run_within_daily_limit("r2", "k", {}, user_id="ali", max_per_day=1)


def test_marking_is_idempotent(tmp_path):
    """Her acilista calisiyor; ikinci turda ayni satirlari tekrar isaretlememeli."""
    _, store = _setup(tmp_path)
    store.create_run_within_daily_limit("r1", "k", {}, user_id="ali", max_per_day=0)

    assert store.mark_interrupted_runs() == 1
    assert store.mark_interrupted_runs() == 0


def test_interrupted_runs_still_appear_in_history(tmp_path):
    """Isaretleniyor da SILINMIYOR: kullanici ne oldugunu gorebilmeli."""
    _, store = _setup(tmp_path)
    store.create_run_within_daily_limit("r1", "konu", {}, user_id="ali", max_per_day=0)
    store.mark_interrupted_runs()

    rows = store.list_runs(user_id="ali", limit=10, offset=0)

    assert [row["run_id"] for row in rows] == ["r1"]
    assert rows[0]["is_complete"] is False
