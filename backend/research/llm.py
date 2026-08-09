"""LLM plumbing for research roles: complete(), role resolution, prompts.

Every LLM call in the research pipeline goes through `complete()`:
- builds the provider via `registry.build_provider` (HP-007: api_key_enc is
  decrypted inside registry, never touched here),
- accumulates `stream_chat` content/usage events under `asyncio.wait_for`,
- estimates usage from chars/4 when the provider returns usage: null
  (LM Studio — PHASE_9 Metrics: the estimate is the PRIMARY path, not a
  fallback) and marks it `estimated: true`,
- strips thinking/response tags (HP-002/HP-008 belt-and-braces; role_models
  already avoids thinking models).

Roles resolve through Phase 8's `pick_local_model` (HP-003 size caps and
HP-008 thinking-model avoidance enforced there). `model_policy='allow_cloud'`
pins the smart role to the active chat provider regardless of kind.
"""

import asyncio
import json
import logging
import re
from pathlib import Path

import db
from providers import registry, role_models

logger = logging.getLogger(__name__)

# Serialize ALL research LLM calls: local servers handle one request at a time
# anyway (HP-003 lesson) and this prevents queue thrash once researcher/writer
# run concurrently with the planner.
LLM_SEM = asyncio.Semaphore(1)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_THINK_RE = re.compile(r"<\s*thinking\s*>.*?<\s*/\s*thinking\s*>", re.S | re.I)
_RESP_RE = re.compile(r"<\s*response\s*>(.*?)<\s*/\s*response\s*>", re.S | re.I)

_ESTIMATE_CHARS_PER_TOKEN = 4


def strip_think(text: str) -> str:
    """Remove thinking blocks entirely (scratch), unwrap <response> tags
    (their inner text is the answer — HP-002/HP-008 belt-and-braces)."""
    text = _RESP_RE.sub(r"\1", _THINK_RE.sub("", text or ""))
    return text.strip()


def extract_json(text: str) -> dict | list | None:
    """Think-strip, then json.loads on: the whole text, the first {...} block
    (re.S, balanced-greedy up to the last }), then the first [...] block.
    Returns None on failure (logged at DEBUG)."""
    clean = strip_think(text or "")
    candidates: list[str] = [clean]
    m = re.search(r"\{.*\}", clean, re.S)
    if m and m.group(0) != clean:
        candidates.append(m.group(0))
    m = re.search(r"\[.*\]", clean, re.S)
    if m and m.group(0) != clean and m.group(0) not in candidates:
        candidates.append(m.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    logger.debug("extract_json: no JSON object/array in %r", (clean or "")[:200])
    return None


async def complete(
    provider_row: dict,
    model: str,
    messages: list[dict],
    *,
    timeout_s: float = 180.0,
) -> dict:
    """One non-streaming research LLM call.

    Returns {"text", "usage", "provider_id", "model"}. Usage is real token
    counts when the provider reports them, else chars/4 with `estimated: True`.
    ProviderHTTPError / TimeoutError propagate — the runner maps them.
    """
    provider = registry.build_provider(provider_row)
    text_parts: list[str] = []
    usage: dict | None = None

    async def _run() -> None:
        nonlocal usage
        async for event in provider.stream_chat(model, messages):
            etype = event["type"]
            if etype == "content":
                text_parts.append(event["text"])
            elif etype == "usage":
                usage = event["usage"]

    async with LLM_SEM:
        await asyncio.wait_for(_run(), timeout=timeout_s)

    text = "".join(text_parts)
    if usage and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
        usage_out = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
    else:
        est_in = sum(len(m.get("content") or "") for m in messages) // _ESTIMATE_CHARS_PER_TOKEN
        est_out = (len(text) // _ESTIMATE_CHARS_PER_TOKEN) + 1
        usage_out = {
            "prompt_tokens": est_in,
            "completion_tokens": est_out,
            "estimated": True,
        }
        logger.debug("complete: provider returned no usage — estimated tokens")
    return {
        "text": strip_think(text),
        "usage": usage_out,
        "provider_id": provider_row["id"],
        "model": model,
    }


async def resolve_roles(
    model_policy: str, smart_override: dict | None = None
) -> dict[str, tuple[dict, str] | None]:
    """Resolve the smart/fast role snapshots for a run.

    Returns {"smart": (provider_row, model) | None, "fast": (provider_row, model) | None}.

    smart resolution order:
      smart_override  -> SELECT * FROM providers WHERE id=$1 (HP-007-safe row);
                         that provider/model verbatim, bypassing policy and
                         caps (explicit user choice). A missing row (deleted
                         provider) leaves smart None -> caller fails the run.
      allow_cloud     -> the active chat provider/model as-is (row includes
                         api_key_enc for registry; HP-007-safe).
      local_only      -> pick_local_model('smart') (active local model preferred).
    fast: ALWAYS pick_local_model('fast') regardless of policy.
    A None role means the caller fails the run (retryable).
    """
    roles: dict[str, tuple[dict, str] | None] = {"smart": None, "fast": None}

    fast = await role_models.pick_local_model("fast")
    if fast is not None:
        roles["fast"] = (fast[0], fast[1])

    if smart_override is not None:
        row = await db.fetchrow(
            "SELECT * FROM providers WHERE id = $1", smart_override["provider_id"]
        )
        if row is not None:
            roles["smart"] = (dict(row), smart_override["model"])
    elif model_policy == "allow_cloud":
        state = await db.fetchrow(
            """
            SELECT p.*, s.active_model
            FROM app_state s
            JOIN providers p ON p.id = s.active_provider_id
            WHERE s.id = TRUE
            """
        )
        if state is not None and state["active_model"]:
            roles["smart"] = (dict(state), state["active_model"])
    else:
        smart = await role_models.pick_local_model("smart")
        if smart is not None:
            roles["smart"] = (smart[0], smart[1])

    if roles["smart"] is None:
        logger.warning("resolve_roles(%s): no smart role available", model_policy)
    if roles["fast"] is None:
        logger.warning("resolve_roles(%s): no fast role available", model_policy)
    return roles


def load_prompt(name: str) -> str:
    """Read a prompt template from research/prompts/ (plain markdown files)."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")