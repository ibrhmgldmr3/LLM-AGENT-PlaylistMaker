"""Dokuman ayristirma: sayfa numaralari ve metin CIKMAMA durumu.

En onemli ayrim `no_text` ile `failed` arasinda. Taranmis (goruntu) bir PDF
GECERLI bir dosyadir, yalnizca metin katmani yoktur; onu "hata" saymak
kullaniciya dosyasinin bozuk oldugunu dusundururdu. Bozuk/desteklenmeyen bir
dosya ise gercekten hatadir. Ikisi kullaniciya farkli seyler soyluyor ve
farkli davranis gerektiriyor.
"""

from __future__ import annotations

import io

import pytest

from src.providers.errors import ProviderPermanentError
from src.services.document_parser import SUPPORTED_EXTENSIONS, parse_document


def _minimal_pdf(pages: list[str]) -> bytes:
    """Metin tasiyan asgari bir PDF uretir.

    `pypdf` yazma API'si sunmuyor ve `reportlab` bu test icin fazladan bir
    bagimlilik olurdu. Elle kurulan PDF, `extract_text()`in gercekten
    calistigini dogrulamak icin yeterli.
    """
    objects: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[{}]/Count {}>>".format(
            " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages))), len(pages)
        ).encode(),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    for index, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 3 0 R>>>>"
            f"/MediaBox[0 0 612 792]/Contents {5 + 2 * index} 0 R>>".encode()
        )
        objects.append(b"<</Length %d>>\nstream\n" % len(content) + content + b"\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref)
    )
    return out.getvalue()


# ------------------------------------------------------------------------ PDF


def test_pdf_pages_are_numbered_from_one(tmp_path):
    path = tmp_path / "not.pdf"
    path.write_bytes(_minimal_pdf(["Kovaryans matrisi kuculur", "Ikinci sayfa entropi"]))

    pages = parse_document(path)

    assert [number for number, _text in pages] == [1, 2]
    assert "Kovaryans" in pages[0][1]
    assert "entropi" in pages[1][1]


def test_pdf_without_a_text_layer_yields_no_pages(tmp_path):
    """Taranmis PDF: gecerli dosya, aranabilir metin yok.

    Cagiran taraf bunu `status="no_text"`a cevirip kullaniciya "taranmis
    olabilir" diyor -- sessizce bos indekslemek, kullanicinin dosyasinin
    arandigini sanmasi demek olurdu.
    """
    path = tmp_path / "taranmis.pdf"
    path.write_bytes(_minimal_pdf(["", ""]))

    assert parse_document(path) == []


def test_corrupt_pdf_is_a_permanent_error(tmp_path):
    path = tmp_path / "bozuk.pdf"
    path.write_bytes(b"bu bir PDF degil")

    with pytest.raises(ProviderPermanentError):
        parse_document(path)


# ----------------------------------------------------------------------- DOCX


def test_docx_paragraphs_are_extracted(tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "not.docx"
    document = docx.Document()
    document.add_paragraph("Kalman filtresi bir durum kestirimi yöntemidir.")
    document.add_paragraph("Ölçüm güncellemesi kovaryansı küçültür.")
    document.save(str(path))

    pages = parse_document(path)

    assert len(pages) == 1
    assert pages[0][0] == 1
    assert "Kalman filtresi" in pages[0][1]
    assert "küçültür" in pages[0][1]


def test_docx_table_cells_are_extracted(tmp_path):
    """Ders notlarinda tanim/formul tablolari sik; yalnizca paragraflara bakan
    bir okuyucu onlari sessizce atlardi."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "tablo.docx"
    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Kovaryans"
    table.rows[0].cells[1].text = "Belirsizlik ölçüsü"
    document.save(str(path))

    pages = parse_document(path)

    assert "Belirsizlik ölçüsü" in pages[0][1]


def test_empty_docx_yields_no_pages(tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "bos.docx"
    docx.Document().save(str(path))

    assert parse_document(path) == []


# ------------------------------------------------------------------ duz metin


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_plain_text_is_read_as_one_page(tmp_path, suffix):
    path = tmp_path / f"not{suffix}"
    path.write_text("# Başlık\n\nKovaryans matrisi küçülür.", encoding="utf-8")

    pages = parse_document(path)

    assert len(pages) == 1
    assert "Kovaryans" in pages[0][1]


def test_broken_encoding_does_not_reject_the_file(tmp_path):
    """Kodlamasi bozuk bir dosya yuzunden yukleme dusmemeli."""
    path = tmp_path / "bozuk.txt"
    path.write_bytes(b"Kovaryans \xff\xfe matrisi")

    pages = parse_document(path)

    assert "Kovaryans" in pages[0][1]


def test_empty_text_file_yields_no_pages(tmp_path):
    path = tmp_path / "bos.txt"
    path.write_text("   \n\n  ", encoding="utf-8")

    assert parse_document(path) == []


# ------------------------------------------------------------------- bicimler


def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "arsiv.zip"
    path.write_bytes(b"PK")

    with pytest.raises(ProviderPermanentError, match="Desteklenmeyen"):
        parse_document(path)


def test_format_is_decided_by_the_declared_filename(tmp_path):
    """Sunucudaki ad uretilmis (`uuid4().hex + uzanti`) olabilir; bicim
    kullanicinin verdigi addan da okunabilmeli."""
    path = tmp_path / "0a1b2c3d.dat"
    path.write_text("Kovaryans matrisi.", encoding="utf-8")

    pages = parse_document(path, filename="ders-notu.txt")

    assert "Kovaryans" in pages[0][1]


def test_supported_extensions_are_a_whitelist():
    assert SUPPORTED_EXTENSIONS == {".pdf", ".docx", ".txt", ".md"}
