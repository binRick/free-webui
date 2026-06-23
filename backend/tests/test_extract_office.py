"""Built-in Office (OOXML) text extraction + the optional external-extractor
passthrough. The OOXML samples are assembled inline with the stdlib zipfile so
the tests don't depend on python-docx/openpyxl — they exercise our own parsers.
"""
import io
import zipfile

import httpx

from app import rag
from app.config import settings


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _docx(*paragraphs: str) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    return _zip({"word/document.xml": doc})


def test_extract_docx_paragraphs():
    data = _docx("Hello world", "Second paragraph")
    text = rag.extract_text("notes.docx", None, data)
    assert text == "Hello world\nSecond paragraph"


def test_extract_docx_joins_runs_in_a_paragraph():
    doc = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t> world</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    data = _zip({"word/document.xml": doc})
    assert rag.extract_text("a.docx", None, data) == "Hello world"


def test_extract_xlsx_shared_and_numeric_cells():
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Alpha</t></si><si><t>Beta</t></si></sst>"
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
        "<row><c><v>42</v></c></row>"
        "</sheetData></worksheet>"
    )
    data = _zip({"xl/sharedStrings.xml": shared, "xl/worksheets/sheet1.xml": sheet})
    text = rag.extract_text("book.xlsx", None, data)
    assert text == "Alpha\tBeta\n42"


def test_extract_pptx_slides_in_order():
    def slide(*runs: str) -> str:
        body = "".join(f"<a:t>{r}</a:t>" for r in runs)
        return (
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f"<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
        )

    data = _zip(
        {
            "ppt/slides/slide2.xml": slide("Slide two"),
            "ppt/slides/slide1.xml": slide("Slide title", "Bullet one"),
        }
    )
    text = rag.extract_text("deck.pptx", None, data)
    # slide1 before slide2 despite archive order
    assert text == "Slide title\nBullet one\n\nSlide two"


def test_unsupported_binary_still_415():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        rag.extract_text("photo.heic", None, b"\x00\x01\x02\xff\xfe")
    assert ei.value.status_code == 415


async def test_external_extractor_preferred_when_configured(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, text="  EXTRACTED VIA TIKA  ")

    monkeypatch.setattr(settings, "content_extraction_url", "http://tika.local/tika")
    monkeypatch.setattr(settings, "content_extraction_api_key", "secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await rag.extract_document_text(client, "scan.pdf", "application/pdf", b"%PDF-1.7")
    assert text == "EXTRACTED VIA TIKA"  # trimmed


async def test_external_extractor_falls_back_to_builtin_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(settings, "content_extraction_url", "http://tika.local/tika")
    monkeypatch.setattr(settings, "content_extraction_api_key", "")
    data = _docx("Local fallback wins")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await rag.extract_document_text(client, "doc.docx", None, data)
    assert text == "Local fallback wins"


async def test_builtin_used_when_no_external_configured(monkeypatch):
    monkeypatch.setattr(settings, "content_extraction_url", "")
    data = _docx("No external service")

    async def _boom(*a, **k):  # would raise if the external path were taken
        raise AssertionError("external extractor must not be called")

    monkeypatch.setattr(rag, "_extract_external", _boom)
    async with httpx.AsyncClient() as client:
        text = await rag.extract_document_text(client, "doc.docx", None, data)
    assert text == "No external service"
