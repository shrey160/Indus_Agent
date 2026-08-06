from pathlib import Path

from pypdf import PdfReader


def load_pdf(path: Path) -> list[tuple[int, str]]:
    reader = PdfReader(str(path))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append((i, text))
    return pages


def load_text(path: Path) -> list[tuple[None, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [(None, text)]


def _total_stripped(pages: list[tuple[int | None, str]]) -> int:
    return sum(len(text.strip()) for _, text in pages)


def load_document(path: Path) -> list[tuple[int | None, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = load_pdf(path)
        if _total_stripped(pages) < 50:
            raise ValueError("scanned PDF? OCR not supported yet")
        return pages
    if suffix in (".md", ".txt"):
        pages = load_text(path)
        if _total_stripped(pages) < 50:
            raise ValueError("file appears empty")
        return pages
    raise ValueError(f"unsupported file type: {suffix}")
