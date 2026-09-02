"""Research run presets, hard caps, and env overrides.

Every hard cap lives here so the runner, planner, and researcher share one source
of truth (PHASE_9 "Guards"). The wall-clock guard is SOFT (spec).
"""

import os

# How many runs may execute in parallel. Local LLM serving serializes anyway
# (HP-003 lesson) — de-facto 1. Env-overridable.
RESEARCH_MAX_CONCURRENT = int(os.environ.get("RESEARCH_MAX_CONCURRENT", "1"))

# Whitelisted numeric keys a caller may override per-run (all caps, LOWER-only).
_OVERRIDABLE = {"tasks", "iterations", "sources_per_task", "tool_calls", "page_chars", "wall_min"}

# Per-depth guards (PHASE_9 Guards table, verbatim).
PRESETS: dict[str, dict] = {
    "quick": {
        "tasks": 3,
        "iterations": 1,
        "sources_per_task": 5,
        "tool_calls": 35,
        "page_chars": 6000,
        "wall_min": 10,
        "fetch_timeout_s": 20,
        "search_concurrency": 3,
        "notes_token_budget": 1500,
    },
    "standard": {
        "tasks": 6,
        "iterations": 2,
        "sources_per_task": 8,
        "tool_calls": 80,
        "page_chars": 8000,
        "wall_min": 30,
        "fetch_timeout_s": 20,
        "search_concurrency": 3,
        "notes_token_budget": 1500,
    },
    "deep": {
        "tasks": 10,
        "iterations": 3,
        "sources_per_task": 12,
        "tool_calls": 140,
        "page_chars": 10000,
        "wall_min": 60,
        "fetch_timeout_s": 20,
        "search_concurrency": 3,
        "notes_token_budget": 1500,
    },
}


def resolve_config(depth: str, overrides: dict | None) -> dict:
    """Resolve a preset merged with per-run overrides.

    Overrides may only LOWER a cap (`min(preset, override)`). Any unknown key or
    depth raises ValueError (routed to a 400 by the caller).

    `role_models` is a forward-compat hook (PHASE_9 Out of Scope): it is accepted
    into the resolved dict verbatim and never acted on here.
    """
    if depth not in PRESETS:
        raise ValueError(f"unknown depth {depth!r}")
    cfg = dict(PRESETS[depth])
    if overrides:
        for key, value in overrides.items():
            if key == "role_models":
                cfg[key] = value
                continue
            if key not in _OVERRIDABLE:
                raise ValueError(f"unknown override key {key!r}")
            cfg[key] = min(value, cfg[key])
    return cfg
