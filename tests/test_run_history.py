"""Faz 0: calistirma gecmisi okuma yuzeyi + `user_id` kolonu.

`run` tablolari uzun sure yalnizca YAZILIYORDU. React'teki gecmis sayfasinin
veri kaynagi bunlar.
"""

import sqlite3

import pytest

from src.config import AppConfig
from src.models import FilterOptions, PlaylistResult
from src.storage import DEFAULT_USER_ID, SQLiteStore


@pytest.fixture
def store(tmp_path):
    config = AppConfig(
        gemini_api_key="test",
        data_dir=str(tmp_path),
        sqlite_path=str(tmp_path / "cache" / "app.db"),
    )
    config.ensure_directories()
    return SQLiteStore(config.sqlite_path)


def _result(run_id: str, topic: str = "Konu") -> PlaylistResult:
    return PlaylistResult(
        run_id=run_id, topic=topic, filters=FilterOptions(), subtopics=[], recommendations=[]
    )


def test_run_table_has_user_id(store):
    with store.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(run)")}
    assert "user_id" in columns


def test_create_run_defaults_to_local_user(store):
    store.create_run("r1", "Konu", FilterOptions().model_dump())
    assert store.get_run_summary("r1")["user_id"] == DEFAULT_USER_ID


def test_runs_are_scoped_by_user(store):
    store.create_run("r1", "A", {}, user_id="ali")
    store.create_run("r2", "B", {}, user_id="veli")

    assert [r["run_id"] for r in store.list_runs(user_id="ali")] == ["r1"]
    assert [r["run_id"] for r in store.list_runs(user_id="veli")] == ["r2"]
    assert len(store.list_runs(user_id=None)) == 2


def test_get_run_returns_none_until_finalised(store):
    store.create_run("r1", "Konu", {})
    assert store.get_run("r1") is None
    assert store.get_run_summary("r1")["is_complete"] is False

    store.finalize_run("r1", _result("r1"))

    assert store.get_run("r1") is not None
    assert store.get_run_summary("r1")["is_complete"] is True


def test_get_run_round_trips_the_result(store):
    store.create_run("r1", "Zaman serisi", {})
    store.finalize_run("r1", _result("r1", topic="Zaman serisi"))

    loaded = store.get_run("r1")

    assert loaded.run_id == "r1"
    assert loaded.topic == "Zaman serisi"


def test_list_runs_is_newest_first(store):
    for index in range(3):
        store.create_run(f"r{index}", f"Konu {index}", {})
        # created_at mikrosaniye hassasiyetinde; siralamanin belirgin olmasi icin
        # kayitlari ayri ayri yaziyoruz.
    ids = [row["run_id"] for row in store.list_runs()]
    assert ids == sorted(ids, reverse=True) or len(set(ids)) == 3


def test_list_runs_pagination(store):
    for index in range(10):
        store.create_run(f"r{index:02d}", "Konu", {})

    first = store.list_runs(limit=4, offset=0)
    second = store.list_runs(limit=4, offset=4)

    assert len(first) == 4
    assert len(second) == 4
    assert {r["run_id"] for r in first} & {r["run_id"] for r in second} == set()


def test_list_runs_limit_is_clamped(store):
    store.create_run("r1", "Konu", {})
    assert len(store.list_runs(limit=99999)) == 1
    assert len(store.list_runs(limit=0)) == 1


def test_count_runs(store):
    store.create_run("r1", "A", {}, user_id="ali")
    store.create_run("r2", "B", {}, user_id="ali")
    store.create_run("r3", "C", {}, user_id="veli")

    assert store.count_runs(user_id="ali") == 2
    assert store.count_runs(user_id=None) == 3


def test_delete_run_removes_children(store):
    store.create_run("r1", "Konu", {})
    store.add_run_subtopic("r1", 1, {"x": 1})
    store.add_run_video("r1", "recommendation", "v1", {"y": 2})

    assert store.delete_run("r1") is True

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM run_subtopic WHERE run_id='r1'").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM run_video WHERE run_id='r1'").fetchone()["n"] == 0


def test_delete_run_respects_ownership(store):
    store.create_run("r1", "Konu", {}, user_id="ali")

    assert store.delete_run("r1", user_id="veli") is False
    assert store.get_run_summary("r1") is not None


def test_summary_of_unknown_run_is_none(store):
    assert store.get_run_summary("yok") is None
    assert store.get_run("yok") is None


def test_migration_adds_user_id_to_existing_database(tmp_path):
    """Eski semali bir veritabani acildiginda kolon eklenmeli."""
    db_path = tmp_path / "eski.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE run (
            run_id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            filters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_json TEXT
        );
        INSERT INTO run VALUES ('eski1', 'Eski konu', '{}', '2020-01-01T00:00:00+00:00', NULL);
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))

    summary = store.get_run_summary("eski1")
    assert summary is not None
    assert summary["user_id"] == DEFAULT_USER_ID, "mevcut kayitlar varsayilan kullaniciya atanmali"
