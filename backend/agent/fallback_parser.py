import json
import re


def _json_spans(text: str) -> list[tuple[int, int, dict]]:
    """Find top-level JSON objects in text that look like tool calls."""
    decoder = json.JSONDecoder()
    spans = []
    idx = 0
    while idx < len(text):
        if text[idx] == "{":
            try:
                obj, end = decoder.raw_decode(text, idx)
                if (
                    isinstance(obj, dict)
                    and "name" in obj
                    and "arguments" in obj
                    and isinstance(obj["name"], str)
                ):
                    spans.append((idx, idx + end, obj))
                    idx += end
                    continue
            except json.JSONDecodeError:
                pass
        idx += 1
    return spans


def parse_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Extract {"name": ..., "arguments": ...} tool calls from plain assistant text.

    Returns (cleaned_text, calls).  calls are normalized to {"name", "arguments"}.
    """
    spans = _json_spans(text)
    if not spans:
        return text, []

    calls = []
    for _start, _end, obj in spans:
        calls.append({"name": obj["name"], "arguments": obj.get("arguments", {})})

    # Remove the matched JSON substrings from the text, preserving surrounding content.
    cleaned = text
    for start, end, _obj in reversed(spans):
        cleaned = cleaned[:start].rstrip() + " " + cleaned[end:].lstrip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, calls
