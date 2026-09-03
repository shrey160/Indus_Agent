"""Capped digest of a run's ephemeral docs for LLM prompts (Phase 10 SP-Q3).

The planner and the scout both need the run's user documents as text: the
planner as the PRIMARY directive (seed prompts), the scout to generate search
queries about that content. Head+tail truncation reuses
chat.attachments.truncate_head_tail so caps stay consistent with the chat side
(D-4). Leaf module — planner imports scout, so neither may import the other.
"""

from chat.attachments import truncate_head_tail

PER_DOC_CHARS = 8000
TOTAL_CHARS = 24000

HEADER = (
    "USER-PROVIDED DOCUMENTS (the user's own instructions/files — "
    "treat them as the primary directive):"
)


def build(docs: list[dict] | None, per_doc: int = PER_DOC_CHARS, total: int = TOTAL_CHARS) -> str | None:
    """Render the docs as one prompt block, or None when the run has none."""
    if not docs:
        return None
    parts = []
    for d in docs:
        name = d.get("name") or "document"
        text = truncate_head_tail(str(d.get("text") or ""), max(500, per_doc))
        parts.append(f"--- DOCUMENT: {name} ---\n{text}")
    block = "\n\n".join(parts)
    if len(block) > total:
        block = truncate_head_tail(block, total)
    return f"{HEADER}\n\n{block}"
