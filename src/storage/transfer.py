"""SQLite'taki veriyi Postgres'e tasir.

`DATABASE_URL`i mevcut bir kurulumda acmak, semasi kurulu ama BOS bir
veritabaniyla baslamak demek: calistirma gecmisi, ogrenme alanlari, OAuth
jetonlari ve kota sayaclari eski dosyada kalir. Bu modul o bir kerelik
aktarimi yapiyor.

    python -m src.storage.transfer                 # kaynak/hedef `.env`'den
    python -m src.storage.transfer --dry-run       # yalnizca sayilari yaz
    python -m src.storage.transfer --truncate      # hedefteki veriyi SIL

Tasarim kararlari:

* **Sema kopyalanmiyor, yeniden kuruluyor.** Hedef depo `SQLiteStore` ile
  aciliyor ve semayi kendi kuruyor (`_ensure_schema`). Semayi tasimak, ceviri
  kurallarinin ikinci bir kopyasini yazmak olurdu -- ve iki kopyanin ayrismasi
  an meselesi.

* **Tablo listesi HESAPLANIYOR.** Elle yazilmis bir liste, yeni bir tablo
  eklendiginde sessizce eksik kalirdi: aktarim "basarili" der, veri gelmez.
  Liste kaynak veritabaninin kendisinden okunuyor.

* **Sirlar COZULMUYOR.** Sifreli sutunlar (OAuth jetonlari, PKCE
  dogrulayicilari) oldugu gibi kopyalaniyor. Yani hedef kurulum AYNI
  `SECRET_ENCRYPTION_KEY` ile calismali; farkli bir anahtarla jetonlar
  okunamaz hale gelir.
"""

from __future__ import annotations

import argparse
import logging

from src.storage.sqlite_store import SQLiteStore

_log = logging.getLogger(__name__)

# Tek `executemany` cagrisinda tasinan satir sayisi.
BATCH_SIZE = 500


def logical_tables(conn) -> list[str]:
    """Kullanicinin verisini tutan tablolar; GOLGE tablolar haric.

    FTS5 sanal tablosu (`chunk_fts`) yaninda kendi ic tablolarini da yaratiyor
    (`chunk_fts_data`, `chunk_fts_idx`, ...). Bunlari kopyalamak ANLAMSIZ ve
    zararli: hedefte `chunk_fts` artik siradan bir tablo ve o ic yapinin
    karsiligi yok. Golgeler, sanal tablonun adiyla baslamalarindan taniniyor --
    adlarini elle listelemek yerine `sqlite_master`dan hesaplaniyor ki yeni bir
    sanal tablo eklendiginde bu kod kendiliginden dogru kalsin.
    """
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master"
        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    names = {row["name"] for row in rows}
    virtual = {
        row["name"]
        for row in rows
        if (row["sql"] or "").upper().lstrip().startswith("CREATE VIRTUAL TABLE")
    }
    shadows = {
        name
        for name in names
        if any(name != base and name.startswith(base + "_") for base in virtual)
    }
    return sorted(names - shadows)


def _columns(conn, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def _sync_sequences(conn, table: str, columns: list[str]) -> None:
    """Postgres'in otomatik id sayacini tasinan en buyuk degere ceker.

    `chunk_id` hedefte `BIGSERIAL`: satirlar ACIK id ile yazildigi icin sayac
    1'de kaliyor ve aktarimdan SONRAKI ilk ekleme "duplicate key" ile
    patliyor. Aktarimin en kolay gozden kacan adimi bu -- veri dogru gorunuyor,
    uygulama ilk yazmada kiriliyor.

    Hangi sutunun sayaci oldugu Postgres'e SORULUYOR
    (`pg_get_serial_sequence`); elle "chunk_id" yazmak, ikinci bir otomatik id
    eklendiginde sessizce eksik kalirdi.
    """
    for column in columns:
        row = conn.execute(
            "SELECT pg_get_serial_sequence(?, ?) AS seq", (table, column)
        ).fetchone()
        sequence = row["seq"] if row else None
        if not sequence:
            continue
        # Ucuncu arguman `is_called`: tablo BOSSA sayac 1'de ve "henuz
        # kullanilmadi" olarak kalmali, yoksa ilk id 2 olurdu.
        conn.execute(
            f"SELECT setval(?, COALESCE((SELECT MAX({column}) FROM {table}), 1),"
            f" (SELECT COUNT(*) FROM {table}) > 0) AS ok",
            (sequence,),
        )


def transfer(
    source: SQLiteStore,
    target: SQLiteStore,
    *,
    truncate: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Tablo tablo kopyalar; `{tablo: satir}` doner.

    Hedefte veri VARSA ve `truncate` verilmediyse hicbir sey yazmadan hata
    veriyor. Yarim dolu bir hedefe eklemek, cakismayan satirlari kopyalayip
    cakisanlarda patlayarak veritabanini ikisinin KARISIMI halinde birakirdi.
    """
    with source.connect() as src_conn:
        tables = logical_tables(src_conn)
        if dry_run:
            return {table: _count(src_conn, table) for table in tables}

        with target.connect() as dst_conn:
            existing = {}
            for table in tables:
                count = _count(dst_conn, table)
                if count:
                    existing[table] = count
            if existing and not truncate:
                raise ValueError(
                    "Hedef veritabani bos degil: "
                    + ", ".join(f"{t}={n}" for t, n in sorted(existing.items()))
                    + ". Once verisini silin ya da --truncate kullanin."
                )
            if truncate:
                for table in reversed(tables):
                    dst_conn.execute(f"DELETE FROM {table}")

            moved: dict[str, int] = {}
            for table in tables:
                columns = _columns(src_conn, table)
                names = ", ".join(columns)
                slots = ", ".join("?" for _ in columns)
                statement = f"INSERT INTO {table} ({names}) VALUES ({slots})"

                cursor = src_conn.execute(f"SELECT {names} FROM {table}")
                written = 0
                while True:
                    rows = cursor.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    dst_conn.executemany(statement, [tuple(row) for row in rows])
                    written += len(rows)

                if target._dialect.name == "postgres":
                    _sync_sequences(dst_conn, table, columns)
                moved[table] = written
                _log.info("%s: %s satir", table, written)

    return moved


def main(argv: list[str] | None = None) -> int:
    from src.config.settings import load_config
    from src.storage.dialect import PostgresDialect

    parser = argparse.ArgumentParser(description="SQLite -> Postgres veri aktarimi")
    parser.add_argument("--source", help="Kaynak SQLite dosyasi (varsayilan: SQLITE_PATH)")
    parser.add_argument("--target-dsn", help="Hedef Postgres DSN (varsayilan: DATABASE_URL)")
    parser.add_argument("--truncate", action="store_true", help="Hedefteki veriyi SIL")
    parser.add_argument("--dry-run", action="store_true", help="Yalnizca kaynak sayilarini yaz")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()

    source_path = args.source or config.sqlite_path
    target_dsn = args.target_dsn or (config.database_url or "").strip()
    if not target_dsn:
        parser.error("Hedef yok: DATABASE_URL tanimlayin ya da --target-dsn verin")

    source = SQLiteStore(source_path, encryption_key=config.secret_encryption_key)
    target = SQLiteStore(
        source_path,
        encryption_key=config.secret_encryption_key,
        dialect=PostgresDialect(target_dsn),
    )

    try:
        result = transfer(source, target, truncate=args.truncate, dry_run=args.dry_run)
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")

    for table, count in sorted(result.items()):
        print(f"  {table:20} {count}")
    label = "Kaynakta" if args.dry_run else "Tasinan"
    print(f"{label} toplam: {sum(result.values())} satir")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
