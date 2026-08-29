"""Lehce cevirilerinin SAF kismi: yer tutucular ve tam metin sorgusu.

Bu iki fonksiyon veritabanina hic dokunmuyor, dolayisiyla Postgres kurulu
olmayan her yerde de kosuyorlar. Onemli olmalarinin sebebi de bu: cevirinin
kirildigi yer, hatanin en gec fark edildigi yer oluyor -- biri `IndexError`
olarak psycopg2'nin icinden, digeri "arama hic sonuc bulmadi" gibi gorunen
SESSIZ bir ariza olarak cikiyor.
"""

from __future__ import annotations

import pytest

from src.storage.dialect import to_pg_placeholders, to_tsquery_expression


# ------------------------------------------------------------- yer tutucular


def test_placeholders_are_translated():
    assert to_pg_placeholders("SELECT ? WHERE a = ?") == "SELECT %s WHERE a = %s"


def test_question_mark_in_a_comment_is_left_alone():
    """BU HATA YASANDI -- hem de bir duzeltmeyi ACIKLAYAN yorumun icinde.

    Yorumdaki `?` cevrilince ifade fazladan bir parametre bekliyor ve psycopg2
    `IndexError: tuple index out of range` veriyor. Naif bir `replace` bunu
    yapiyordu; guvence "kaynakta yorumlarda `?` yok" olcumune dayaniyordu ve o
    olcum ilk yazida kirildi.
    """
    statement = "SELECT a -- kac tane? bir tane\nFROM t WHERE b = ?"

    assert to_pg_placeholders(statement) == (
        "SELECT a -- kac tane? bir tane\nFROM t WHERE b = %s"
    )


def test_question_mark_in_a_string_literal_is_left_alone():
    assert to_pg_placeholders("SELECT '?' , ?") == "SELECT '?' , %s"


def test_escaped_quote_does_not_end_the_literal():
    """`''` SQL'de kacistir; literalin bittigi sanilirsa sonrasi yanlis okunur."""
    assert to_pg_placeholders("SELECT 'a''?b', ?") == "SELECT 'a''?b', %s"


# ----------------------------------------------------------- tam metin sorgusu


@pytest.mark.parametrize(
    "fts, tsquery",
    [
        ("kalman", "kalman"),
        # Onek yildizi: FTS5'te `token*`, Postgres'te `token:*`.
        ("kalman*", "kalman:*"),
        ("kalman* OR filtre*", "kalman:* | filtre:*"),
        ("kalman AND filtre", "kalman & filtre"),
    ],
)
def test_supported_grammar_is_translated(fts, tsquery):
    assert to_tsquery_expression(fts) == tsquery


@pytest.mark.parametrize(
    "fts",
    [
        "",
        "   ",
        'dengesiz " tirnak',       # kullanicidan gelebilecek bozuk sorgu
        "kalman OR",               # operatorle bitiyor
        "OR kalman",               # operatorle basliyor
        "kalman filtre",           # ortulu operator: FTS5'te AND, burada belirsiz
        "kalman | filtre",         # tsquery yazimi FTS5 yazimi degil
        "NEAR(a b)",               # destegi olmayan FTS5 ozelligi
        "kalman & filtre; DROP",   # kacisi degil BEYAZ LISTEYI sinar
    ],
)
def test_unsupported_input_is_rejected_whole(fts):
    """Taninmayan parca ATILMIYOR, ifadenin TAMAMI reddediliyor.

    Atmak sorgunun anlamini SESSIZCE degistirirdi ("kalman AND filtre" ->
    "kalman"). Reddetmek ise cagiranin zaten bekledigi "bos sonuc" yoluna
    dusuyor -- sorguyu kullanici yaziyor ve bir soru yuzunden 500 donmek
    yanlis olurdu.
    """
    assert to_tsquery_expression(fts) == ""


def test_lowercase_or_is_a_token_not_an_operator():
    """FTS5'te operatorler BUYUK harf; kucugu siradan bir kelime.

    Ayrimi korumak sart: `or` bir operator sayilsaydi metinde gecen kelimeyi
    aramak yerine sorgunun yapisi degisirdi.
    """
    assert to_tsquery_expression("kalman or filtre") == ""
