"""Text extraction for supported document and image uploads."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from docx import Document
from pypdf import PdfReader


class OCRUnavailable(RuntimeError):
    pass


def validate_upload(data: bytes, suffix: str) -> None:
    """Check real file signatures and bound parser expansion before storage or OCR."""
    suffix = suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("The file does not contain a PDF signature")
        return
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2048 or sum(entry.file_size for entry in entries) > 100_000_000:
                    raise ValueError("The DOCX package exceeds safe processing limits")
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise ValueError("The file is not a valid Word document")
                if any(entry.file_size > 20_000_000 for entry in entries):
                    raise ValueError("The DOCX package contains an oversized component")
                if archive.testzip() is not None:
                    raise ValueError("The DOCX package contains a corrupt component")
        except zipfile.BadZipFile as exc:
            raise ValueError("The file is not a valid DOCX package") from exc
        return
    expected_formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if suffix in expected_formats:
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = 40_000_000
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected_formats[suffix] or image.width * image.height > 40_000_000:
                    raise ValueError("Image format or dimensions do not match safe upload limits")
                image.verify()
        except ImportError as exc:
            raise OCRUnavailable("Install Pillow to validate image uploads") from exc
        except Exception as exc:
            raise ValueError("The file is not a valid image of the declared type") from exc
        return
    raise ValueError("Unsupported document type")


def ocr_image(data: bytes, language: str = "eng") -> str:
    try:
        from PIL import Image, ImageOps
        import pytesseract
        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.width * image.height > 40_000_000:
                raise ValueError("Image dimensions exceed the processing limit")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            return pytesseract.image_to_string(normalized, lang=language, timeout=20).strip()
    except ImportError as exc:
        raise OCRUnavailable("Install Pillow and pytesseract to enable local OCR") from exc
    except Exception as exc:
        try:
            import pytesseract
            if isinstance(exc, pytesseract.TesseractNotFoundError):
                raise OCRUnavailable("Install the Tesseract OCR engine to extract image text") from exc
        except ImportError:
            pass
        raise


def _ocr_pdf_page(document, page_index: int, language: str) -> str:
    try:
        import pytesseract
        page = document[page_index]
        bitmap = page.render(scale=2, rotation=0)
        image = bitmap.to_pil()
        try:
            return pytesseract.image_to_string(image, lang=language, timeout=25).strip()
        finally:
            image.close()
            bitmap.close()
            page.close()
    except ImportError as exc:
        raise OCRUnavailable("Install pypdfium2, Pillow, and pytesseract to OCR scanned PDFs") from exc
    except Exception as exc:
        try:
            import pytesseract
            if isinstance(exc, pytesseract.TesseractNotFoundError):
                raise OCRUnavailable("Install the Tesseract OCR engine to extract scanned PDF text") from exc
        except ImportError:
            pass
        raise


def extract_pdf(data: bytes, language: str = "eng", max_pages: int = 50) -> tuple[str, list[int]]:
    reader = PdfReader(io.BytesIO(data), strict=False)
    if len(reader.pages) > max_pages:
        raise ValueError(f"PDF exceeds the {max_pages}-page processing limit")
    pdfium = None
    output = []
    ocr_pages = []
    for index, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if not text:
            if pdfium is None:
                try:
                    import pypdfium2 as pdfium_module
                    pdfium = pdfium_module.PdfDocument(data)
                except ImportError as exc:
                    raise OCRUnavailable("Install pypdfium2 to process scanned PDF pages") from exc
            text = _ocr_pdf_page(pdfium, index, language)
            if text:
                ocr_pages.append(index + 1)
        output.append(f"[Page {index + 1}]\n{text}" if text else f"[Page {index + 1}]\n[No text detected]")
    if pdfium is not None:
        pdfium.close()
    return "\n\n".join(output).strip(), ocr_pages


def extract_docx(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    parts = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            style = paragraph.style.name if paragraph.style else ""
            parts.append(f"[{style}] {text}" if style.lower().startswith("heading") else text)
    for table_index, table in enumerate(document.tables, 1):
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(f"[Table {table_index}] " + " | ".join(cells))
    return "\n".join(parts)[:50000]


def extract_upload(data: bytes, suffix: str, language: str = "eng") -> tuple[str, list[int]]:
    suffix = suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(data, language)
    if suffix == ".docx":
        return extract_docx(data), []
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return ocr_image(data, language), []
    raise ValueError("Unsupported document type")


def guideline_candidates(data: bytes, filename: str, suffix: str, language: str = "eng") -> list[dict[str, str]]:
    """Convert document passages into pending admin-review records; never activates extracted text."""
    passages: list[tuple[str, str]] = []
    if suffix.lower() == ".pdf":
        text, _ = extract_pdf(data, language)
        for page_block in re.split(r"\n\n(?=\[Page \d+\])", text):
            match = re.match(r"\[Page (\d+)\]\n", page_block)
            if not match:
                continue
            page_number = match.group(1)
            for paragraph in re.split(r"\n\s*\n|\n", page_block[match.end():]):
                passage = re.sub(r"\s+", " ", paragraph).strip()
                if len(passage) >= 12 and passage != "[No text detected]":
                    passages.append((f"{filename}, page {page_number}", passage))
    elif suffix.lower() == ".docx":
        document = Document(io.BytesIO(data))
        heading = "Document"
        for paragraph in document.paragraphs:
            passage = re.sub(r"\s+", " ", paragraph.text).strip()
            if not passage:
                continue
            style = paragraph.style.name if paragraph.style else ""
            if style.lower().startswith("heading"):
                heading = passage[:160]
            elif len(passage) >= 12:
                passages.append((f"{filename}, section {heading}", passage))
        for table_index, table in enumerate(document.tables, 1):
            for row_index, row in enumerate(table.rows, 1):
                passage = " | ".join(re.sub(r"\s+", " ", cell.text).strip() for cell in row.cells)
                if len(passage) >= 12:
                    passages.append((f"{filename}, table {table_index} row {row_index}", passage))
    else:
        raise ValueError("Guidelines must be PDF or DOCX")
    candidates = []
    for location, passage in passages[:100]:
        title = passage.split(".", 1)[0].strip()[:240] or "Imported guideline passage"
        category = "legal" if re.search(r"\b(legal|disclaimer|must not|prohibited|compliance)\b", passage, re.I) else "general"
        candidates.append({"kind": "rule", "title": title, "body": passage[:12000], "category": category, "severity": "medium", "source": location, "passage": passage[:12000]})
    return candidates
