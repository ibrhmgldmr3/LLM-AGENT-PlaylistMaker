"""Depolama katmaninin veritabanina bagli KUCUK yuzeyi.

`sqlite_store` icindeki 127 SQL ifadesinin neredeyse tamami iki veritabaninda
da aynen calisiyor -- upsert'ler `ON CONFLICT`e, uretilen anahtar okumasi
`RETURNING`e cevrildikten sonra geriye olculebilir bir fark listesi kaldi:

    yer tutucu     `?`            -> `%s`
    otomatik id    AUTOINCREMENT  -> BIGSERIAL
    ikili veri     BLOB           -> BYTEA
    tam metin      FTS5 sanal tbl -> tsvector
    ayar           PRAGMA         -> (yok)
    serilestirme   BEGIN IMMEDIATE-> pg_advisory_xact_lock

Bu modul o listeyi TEK YERDE tutuyor. Store'un SQL'i degismiyor: SQLite
yazimiyla yaziliyor, Postgres tarafinda cevriliyor. Iki ayri store sinifi
yazmak 1761 satiri ikiye katlar ve ayrisma an meselesi olurdu.

Yer tutucu cevirisi YAPISAL olarak guvenli: `to_pg_placeholders` yorumlarin
ve dize literallerinin ICINI atliyor. Once naif bir `replace` vardi ve
"kaynakta dize icinde `?` yok" olcumune dayaniyordu; o guvence bir yazarin
aklinda tutmasi gereken kurala donusuyordu ve ilk denemede kirildi -- hem de
bir duzeltmeyi aciklayan YORUMUN icinde gecen `?` yuzunden.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol

# SQLite'in yazma kilidini bekleme suresi. Postgres'te karsiligi yok
# (orada bekleme `lock_timeout` ile sunucu tarafinda).
BUSY_TIMEOUT_SEC = 30.0


class Dialect(Protocol):
    """Store'un veritabanindan bekledigi her sey."""

    name: str
    # Eski SEMA gocleri calistirilmali mi. Bu gocler, uygulamanin ONCEKI
    # surumlerinin biraktigi SQLite dosyalarini onarmak icin var (eksik sutun
    # ekleme, kaldirilmis bir tablodan veri kopyalama) ve `PRAGMA table_info`
    # gibi SQLite'a ozgu arac kullaniyorlar. Postgres veritabani TANIM GEREGI
    # bu kodun kendisi tarafindan yeni olusturuluyor: onarilacak gecmis yok.
    supports_legacy_migration: bool

    def connect(self) -> Any: ...

    def sql(self, statement: str) -> str:
        """Store'un SQLite yazimini bu lehceye cevirir."""

    def ddl(self, script: str) -> str:
        """Sema betigini bu lehceye cevirir."""

    def lock(self, conn: Any, key: str) -> None:
        """Islemi SERILESTIRIR. Bkz. `SqliteDialect.lock`."""

    def fts_search(
        self, space_id: str, match_query: str, limit: int
    ) -> tuple[str, tuple] | None:
        """Leksik arama icin `(ifade, parametreler)`; ifade edilemiyorsa `None`.

        Iki lehcenin tam metin yolu ORTAK bir yazima indirgenemiyor: FTS5
        `MATCH` + `bm25()` kullaniyor, Postgres `@@` + `ts_rank`. Ceviri
        yalnizca sozdizimi de degil: `bm25()` NEGATIF doner (daha negatif =
        daha iyi), `ts_rank` POZITIF (daha buyuk = daha iyi). Isaret
        karisikligi tam da sessizce TERS siralama uretecek turden bir hata --
        bu yuzden iki lehce de "score BUYUKSE daha iyi" sozunu veriyor ve
        isaret cevirisi burada, tek yerde yapiliyor.

        `None`: sorgu bu lehcenin sorgu diline cevrilemedi. Cagiran bos sonuc
        donmeli -- sorgu metnini kullanici yaziyor, bir soru yuzunden 500
        donmek yanlis olur.
        """

    @property
    def query_errors(self) -> tuple[type[Exception], ...]:
        """Bozuk bir SORGU metninin uretebilecegi istisnalar.

        Dar tutuluyor: baglanti hatalari buraya girmemeli, cunku onlari yutmak
        "arama bir sey bulamadi" gibi gorunen sessiz bir ariza olurdu.
        """
        return ()


# --------------------------------------------------------------------- SQLite


class SqliteDialect:
    """Bugunku davranis. Hicbir ceviri yapmiyor -- kaynak yazim zaten bu."""

    name = "sqlite"
    supports_legacy_migration = True

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Veritabani DOSYASININ dizini: bu, SQLite'a ozgu bir hazirlik.
        # Eskiden store yapiyordu ve Postgres kullanan bir kurulumda da bos
        # bir `data/cache/` dizini aciliyordu.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=BUSY_TIMEOUT_SEC)
        conn.row_factory = sqlite3.Row
        try:
            # SIRA ONEMLI: once `busy_timeout`, sonra `journal_mode`. Ikincisi
            # kilit bekleyebiliyor ve zaman asimi once kurulmus olmali.
            conn.execute("PRAGMA busy_timeout=%d" % int(BUSY_TIMEOUT_SEC * 1000))
            _enable_wal(conn)
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def sql(self, statement: str) -> str:
        return statement

    def ddl(self, script: str) -> str:
        return script

    def fts_search(
        self, space_id: str, match_query: str, limit: int
    ) -> tuple[str, tuple] | None:
        # `-bm25(...)`: isaret SQL tarafinda cevriliyor. Eskiden Python
        # tarafinda cevriliyordu; iki lehce ayni sozu (buyuk = iyi) verecekse
        # cevirinin de iki lehcede AYNI yerde olmasi gerekiyor.
        return (
            "SELECT chunk_id, -bm25(chunk_fts) AS score FROM chunk_fts"
            " WHERE space_id = ? AND chunk_fts MATCH ?"
            " ORDER BY score DESC LIMIT ?",
            (space_id, match_query, limit),
        )

    @property
    def query_errors(self) -> tuple[type[Exception], ...]:
        # Bozuk FTS5 sorgusu (dengesiz tirnak, yalniz basina `AND`) burada
        # `OperationalError` olarak gelir.
        return (sqlite3.OperationalError,)

    def lock(self, conn: sqlite3.Connection, key: str) -> None:
        """`BEGIN IMMEDIATE`: yazma kilidini HEMEN alir.

        Okuma ile yazma ayri islemlerde kalirsa iki es zamanli istek de ayni
        (eski) sayiyi okuyup ikisi de gecebiliyor -- kota kabulunde bu
        olculdu. `key` SQLite'ta kullanilmiyor: kilit veritabani genelinde.
        """
        conn.execute("BEGIN IMMEDIATE")


def _enable_wal(conn: sqlite3.Connection) -> None:
    """WAL'i acar; kilitliyse SESSIZCE gecer.

    `journal_mode` degisimi dosya duzeyinde ozel kilit istiyor ve SQLite bu
    islemde `busy_timeout`u BEKLEMEDEN `SQLITE_BUSY` dondurebiliyor. Es zamanli
    acilislarda bu "database is locked" olarak disari vuruyordu. WAL dosyanin
    KALICI bir ozelligi: bir kez ayarlandiginda oyle kaliyor.
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" not in message and "busy" not in message:
            raise


# ------------------------------------------------------------------- Postgres

def to_pg_placeholders(statement: str) -> str:
    """`?` -> `%s`, ama YALNIZCA gercek yer tutucular icin.

    Naif `replace` yeterli DEGIL: SQL yorumlarindaki ve dize literallerindeki
    `?` de cevrilir, ifade fazladan yer tutucu bekler ve psycopg2
    `IndexError: tuple index out of range` verir.

    Bu tam olarak yasandi -- hem de bir duzeltmeyi ACIKLAYAN yorumun icinde
    gecen `?` yuzunden. Kaynakta bugun dize literali icinde `?` bulunmuyor
    (olculdu) ama bu, yazarin aklinda tutmasi gereken bir kural olmamali:
    guvence yapisal olmali.

    `/* */` blok yorumu bu kod tabaninda kullanilmiyor; eklenirse buraya da
    eklenmeli.
    """
    out: list[str] = []
    index, length = 0, len(statement)
    while index < length:
        char = statement[index]
        if statement.startswith("--", index):
            end = statement.find("\n", index)
            end = length if end == -1 else end
            out.append(statement[index:end])
            index = end
        elif char == "'":
            end = index + 1
            while end < length:
                if statement[end] == "'":
                    if end + 1 < length and statement[end + 1] == "'":
                        end += 2  # SQL'de kacis: '' -> tek tirnak
                        continue
                    break
                end += 1
            out.append(statement[index : end + 1])
            index = end + 1
        elif char == "?":
            out.append("%s")
            index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)

# Sema cevirileri. Sirali: `AUTOINCREMENT` once, cunku `INTEGER PRIMARY KEY`
# kalibinin icinde geciyor.
_DDL_RULES: tuple[tuple[str, str], ...] = (
    ("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"),
    ("INTEGER PRIMARY KEY", "BIGINT PRIMARY KEY"),
    (" BLOB ", " BYTEA "),
)


# `build_fts_query` ciktisinin TAMAMI: `[a-z0-9]+`, istege bagli sonda `*`.
# Baska hicbir sey uretilmiyor -- ozel karakterlerin sizamamasi o fonksiyonun
# bilincli bir ozelligi (bkz. `text_utils.build_fts_query`).
_FTS_TOKEN = re.compile(r"^[a-z0-9]+\*?$", re.I)

# FTS5'te operatorler BUYUK harf; kucuk harfli `or` siradan bir token. Ceviri
# de ayni ayrimi yapiyor, yoksa "veya or" gibi bir sorgunun anlami degisirdi.
_FTS_OPERATORS = {"OR": "|", "AND": "&"}


def to_tsquery_expression(match_query: str) -> str:
    """FTS5 sorgusunu `to_tsquery` ifadesine cevirir; cevrilemezse `""`.

    Yalnizca `build_fts_query`nin URETTIGI dilbilgisi taniniyor: tokenlar,
    istege bagli onek yildizi ve aralarinda `OR`/`AND`. Taninmayan tek bir
    parca varsa ifadenin TAMAMI reddediliyor.

    Reddetmek, taninmayani atmaktan iyi: atmak sorgunun anlamini SESSIZCE
    degistirir ("kalman AND filtre" -> "kalman"), reddetmek ise cagiranin
    zaten bekledigi "bos sonuc" yoluna dusuyor.

    Ayni sebeple burasi bir KACIS (escaping) noktasi da: kullanici metni
    dogrudan `to_tsquery`ye verilseydi tirnak ya da `&` gibi karakterler
    sozdizimi hatasi uretirdi. Beyaz liste, kacistan daha dar ve daha guvenli.
    """
    parts = match_query.split()
    if not parts:
        return ""
    out: list[str] = []
    expect_token = True
    for part in parts:
        if expect_token:
            if not _FTS_TOKEN.match(part):
                return ""
            # FTS5'te onek `token*`, Postgres'te `token:*`.
            out.append(part[:-1] + ":*" if part.endswith("*") else part)
        else:
            operator = _FTS_OPERATORS.get(part)
            if operator is None:
                return ""
            out.append(operator)
        expect_token = not expect_token
    if expect_token:  # ifade bir operatorle bitti
        return ""
    return " ".join(out)


class PostgresDialect:
    """Ayni SQL'i Postgres'e cevirir."""

    name = "postgres"
    supports_legacy_migration = False

    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def connect(self) -> Iterator[Any]:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield _PgConnection(conn, self)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def sql(self, statement: str) -> str:
        return to_pg_placeholders(statement)

    def ddl(self, script: str) -> str:
        for old, new in _DDL_RULES:
            script = script.replace(old, new)
        # PRAGMA Postgres'te yok; sema betiginde gecmiyor ama gecerse atilmali.
        script = re.sub(r"^\s*PRAGMA[^;]*;", "", script, flags=re.M)
        return _rewrite_fts(script)

    def fts_search(
        self, space_id: str, match_query: str, limit: int
    ) -> tuple[str, tuple] | None:
        expression = to_tsquery_expression(match_query)
        if not expression:
            return None
        # `to_tsquery` FROM icinde tek bir alt sorguda: parametre BIR KEZ
        # geciyor ve store'un parametre sirasi (`space_id, sorgu, limit`)
        # korunuyor. Dogrudan `ts_rank(tsv, to_tsquery(?))` yazmak ayni
        # parametreyi iki kez isterdi.
        #
        # `simple` yapilandirmasi: metin zaten `text_utils.search_key`ten
        # geciyor. Dile dayali bir sozluk (`turkish`) IKINCI bir
        # normallestirme katmani olurdu ve iki taraf ayrisirdi.
        return (
            "SELECT chunk_id, ts_rank(tsv, q.tq) AS score"
            " FROM chunk_fts, (SELECT to_tsquery('simple', ?) AS tq) q"
            " WHERE space_id = ? AND tsv @@ q.tq"
            " ORDER BY score DESC LIMIT ?",
            (expression, space_id, limit),
        )

    @property
    def query_errors(self) -> tuple[type[Exception], ...]:
        import psycopg2

        # `ProgrammingError`: sozdizimi/tsquery hatalari. `DataError`: gecersiz
        # metin gosterimi. `OperationalError` BILEREK disarida -- o baglanti
        # arizasi ve yutulursa "arama bos dondu" gibi gorunurdu.
        return (psycopg2.ProgrammingError, psycopg2.DataError)

    def lock(self, conn: Any, key: str) -> None:
        """`pg_advisory_xact_lock`: islem bitene kadar tutulan tavsiye kilidi.

        Postgres'te `BEGIN IMMEDIATE` gibi bir kip YOK. Alternatif SERIALIZABLE
        yalitim seviyesiydi; o, serilestirme hatasinda cagiranin islemi
        TEKRARLAMASINI gerektiriyor ve kota kabulu gibi tek atislik bir yolda
        bu, her cagri yerine yeniden deneme dongusu eklemek demekti. Tavsiye
        kilidi ayni garantiyi cagiran tarafi degistirmeden veriyor.

        `key` mantiksal kaynagin adi; ayni ada iki islem ayni anda giremiyor.
        """
        # `?` yaziliyor ve normal ceviri yolundan gecip `%s` oluyor -- burada
        # elle `%s` yazmak, cevirinin atlandigi tek istisna olurdu.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))


def _rewrite_fts(script: str) -> str:
    """FTS5 sanal tablosunu `tsvector`li gercek tabloya cevirir.

    FTS5'in Postgres'te dogrudan karsiligi yok. `search_text` zaten
    `text_utils.search_key()`ten geciyor (Turkce harfler Latin karsiligina
    indirgenmis), bu yuzden `simple` yapilandirmasi yeterli -- dil bilgisine
    dayali bir sozluk (`turkish`) ikinci bir normallestirme katmani ekleyip
    sorgu tarafiyla ayrisirdi.
    """
    pattern = re.compile(
        r"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5\((.*?)\);", re.S
    )
    replacement = (
        "CREATE TABLE IF NOT EXISTS chunk_fts (\n"
        "                    search_text TEXT NOT NULL,\n"
        "                    chunk_id BIGINT NOT NULL,\n"
        "                    space_id TEXT NOT NULL,\n"
        "                    tsv tsvector GENERATED ALWAYS AS"
        " (to_tsvector('simple', search_text)) STORED\n"
        "                );\n"
        "                CREATE INDEX IF NOT EXISTS idx_chunk_fts_tsv"
        " ON chunk_fts USING GIN (tsv);\n"
        "                CREATE INDEX IF NOT EXISTS idx_chunk_fts_space"
        " ON chunk_fts (space_id);"
    )
    return pattern.sub(replacement, script)


class _PgCursor:
    """`sqlite3.Cursor`in store tarafindan kullanilan yuzeyi.

    Yalnizca gercekten cagrilan uyeler var: `fetchone`, `fetchall`, `rowcount`
    ve dolasim. Eksik birini eklemek yerine olculdu -- store'un kullandigi
    yuzey bu kadar.
    """

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def __iter__(self):
        return iter(self._cursor)


class _PgConnection:
    """`sqlite3.Connection`in store tarafindan kullanilan yuzeyi.

    SQL'i cevirmek icin var: store `?` yazmaya devam ediyor, burada `%s`
    oluyor. 93 cagri yerine dokunmadan tek noktada.
    """

    def __init__(self, conn, dialect: PostgresDialect):
        self._conn = conn
        self._dialect = dialect

    def execute(self, statement: str, params: tuple = ()) -> _PgCursor:
        cursor = self._conn.cursor()
        cursor.execute(self._dialect.sql(statement), params)
        return _PgCursor(cursor)

    def executemany(self, statement: str, rows) -> _PgCursor:
        cursor = self._conn.cursor()
        cursor.executemany(self._dialect.sql(statement), list(rows))
        return _PgCursor(cursor)

    def executescript(self, script: str) -> None:
        """`executescript` psycopg2'de yok; betik oldugu gibi calistiriliyor.

        Postgres coklu ifadeyi tek `execute` ile kabul ediyor, yani ayirmaya
        gerek yok -- ve ayirmaya calismak yorumlardaki noktali virgullerde
        kirilirdi (bir kez yasandi).
        """
        cursor = self._conn.cursor()
        cursor.execute(self._dialect.ddl(script))
