import logging
import os

import db
from providers import registry
from providers.base import ModelInfo

logger = logging.getLogger(__name__)

MIN_LOCAL_MODEL_BYTES = 1_000_000
LOCAL_ROLE_MAX_BYTES = int(os.environ.get("LOCAL_ROLE_MAX_BYTES", "6000000000"))


def is_real_local_model(provider_type: str, model: ModelInfo) -> tuple[bool, str | None]:
    """HP-008 / HP-003 eligibility filter for background LLM roles.

    Rejects Ollama remote `:cloud` stubs, embedding-only models, and anything
    smaller than 1 MB or larger than LOCAL_ROLE_MAX_BYTES.
    """
    if ":cloud" in model.id:
        return False, "cloud stub"
    if "embed" in model.id.lower():
        return False, "embedding-only name"
    if "speech" in model.id.lower() or "parakeet" in model.id.lower() or "canary" in model.id.lower():
        return False, "non-chat (speech/asr) model"
    size = model.size_bytes
    if size is not None:
        if size < MIN_LOCAL_MODEL_BYTES:
            return False, f"size {size} < {MIN_LOCAL_MODEL_BYTES}"
        if size > LOCAL_ROLE_MAX_BYTES:
            return False, f"size {size} > cap {LOCAL_ROLE_MAX_BYTES}"
    return True, None


def _provider_pref_key(item: tuple[dict, ModelInfo]) -> int:
    """openai-compat local providers (e.g. LM Studio) before Ollama."""
    row, _model = item
    return 0 if row["type"] == "openai" else 1


async def pick_local_model(role: str) -> tuple[dict, str] | None:
    """Pick a safe local model for a background LLM role.

    role: 'fast' | 'smart'
        fast  -> smallest eligible model (extraction, titles, light research)
        smart -> active chat model if local+eligible, else largest eligible,
                 preferring an already-loaded Ollama model.

    Returns (provider_row_dict, model_id) or None if nothing qualifies.
    """
    if role not in {"fast", "smart"}:
        raise ValueError(f"unknown role {role!r}")

    rows = await db.fetch("SELECT * FROM providers WHERE kind = 'local' ORDER BY id")
    if not rows:
        logger.debug("pick_local_model(%s): no local providers", role)
        return None

    statuses = await registry.detect_all()
    candidates: list[tuple[dict, ModelInfo]] = []
    for row in rows:
        status = statuses.get(row["id"])
        if status is None or status.state != "up" or not status.models:
            logger.debug(
                "pick_local_model(%s): provider %s/%s not usable (%s)",
                role,
                row["id"],
                row["name"],
                status.state if status else "missing",
            )
            continue
        for model in status.models:
            ok, reason = is_real_local_model(row["type"], model)
            if not ok:
                logger.debug(
                    "pick_local_model(%s): rejected %s/%s — %s",
                    role,
                    row["name"],
                    model.id,
                    reason,
                )
                continue
            candidates.append((dict(row), model))

    if not candidates:
        logger.info("pick_local_model(%s): no eligible local model", role)
        return None

    candidates.sort(key=_provider_pref_key)

    if role == "fast":
        def _fast_key(item: tuple[dict, ModelInfo]) -> tuple[int, int]:
            _row, model = item
            size = model.size_bytes if model.size_bytes is not None else 2**63
            return (_provider_pref_key(item), size)

        chosen_row, chosen_model = min(candidates, key=_fast_key)
        logger.info(
            "pick_local_model(fast): %s/%s (size=%s)",
            chosen_row["name"],
            chosen_model.id,
            chosen_model.size_bytes,
        )
        return chosen_row, chosen_model.id

    active = await db.fetchrow(
        """
        SELECT s.active_provider_id, s.active_model, p.kind
        FROM app_state s
        LEFT JOIN providers p ON p.id = s.active_provider_id
        WHERE s.id = TRUE
        """
    )
    if active and active["active_provider_id"] and active["kind"] == "local":
        active_pid = active["active_provider_id"]
        active_model = active["active_model"]
        active_item = next(
            (
                (row, model)
                for row, model in candidates
                if row["id"] == active_pid and model.id == active_model
            ),
            None,
        )
        if active_item:
            logger.info(
                "pick_local_model(smart): active chat model %s/%s",
                active_item[0]["name"],
                active_item[1].id,
            )
            return active_item[0], active_item[1].id

    def _smart_size(item: tuple[dict, ModelInfo]) -> int:
        _row, model = item
        return model.size_bytes if model.size_bytes is not None else -1

    candidates.sort(key=_smart_size, reverse=True)
    for row, model in candidates:
        provider = registry.build_provider(row)
        try:
            if await provider.is_loaded(model.id):
                logger.info(
                    "pick_local_model(smart): loaded model %s/%s (size=%s)",
                    row["name"],
                    model.id,
                    model.size_bytes,
                )
                return row, model.id
        except Exception as exc:
            logger.debug(
                "pick_local_model(smart): is_loaded failed for %s/%s: %s",
                row["name"],
                model.id,
                exc,
            )

    chosen_row, chosen_model = candidates[0]
    logger.info(
        "pick_local_model(smart): largest model %s/%s (size=%s)",
        chosen_row["name"],
        chosen_model.id,
        chosen_model.size_bytes,
    )
    return chosen_row, chosen_model.id
