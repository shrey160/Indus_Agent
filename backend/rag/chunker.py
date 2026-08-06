import re

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _find_split(text: str, start: int, end: int) -> int:
    """Find the best boundary at or before end for a chunk starting at start."""
    if end >= len(text):
        return len(text)

    # 1. Paragraph boundary
    para = text.rfind("\n\n", start, end)
    if para != -1 and para > start:
        return para

    # 2. Sentence boundary
    for match in reversed(list(SENTENCE_RE.finditer(text, start, end))):
        if match.end() > start:
            return match.end()

    # 3. Word boundary (last whitespace)
    ws = text.rfind(" ", start, end)
    if ws != -1 and ws > start:
        return ws

    # 4. Hard cut
    return end


def chunk_document(
    pages: list[tuple[int | None, str]], target: int = 2000, overlap: int = 200
) -> list[dict]:
    # Flatten pages, preserving which page each character belongs to.
    parts: list[tuple[int | None, str]] = []
    for page_no, text in pages:
        if text:
            parts.append((page_no, text))
            parts.append((page_no, "\n\n"))
    if parts:
        parts.pop()  # remove trailing separator

    text = "".join(seg for _, seg in parts)
    page_for_pos: list[int | None] = [None] * len(text)
    cursor = 0
    for page_no, seg in parts:
        for i in range(len(seg)):
            page_for_pos[cursor + i] = page_no
        cursor += len(seg)

    chunks: list[dict] = []
    idx = 0
    cursor = 0
    while cursor < len(text):
        remaining = text[cursor:].strip()
        if not remaining:
            break

        end = min(cursor + target, len(text))
        if end < len(text):
            end = _find_split(text, cursor, end)

        content = text[cursor:end].strip()
        if content:
            chunks.append({"idx": idx, "content": content, "page": page_for_pos[cursor]})
            idx += 1

        if end >= len(text):
            break

        # Next chunk overlaps by up to `overlap` chars, snapped to a word boundary.
        overlap_start = max(cursor + 1, end - overlap)
        ws = text.rfind(" ", overlap_start, end)
        if ws != -1:
            cursor = ws + 1
        else:
            cursor = overlap_start

    return chunks
