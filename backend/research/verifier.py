"""VERIFY stage: citation integrity + per-section support + contradiction check.

Three passes (PHASE_9 VERIFY):
1. Citation integrity is PROGRAMMATIC (no LLM): every `[n]` in the report body
   must map to a `research_sources` row. Dangling markers are stripped and the
   sentence annotated `[unverified]`. Sources never cited in the body are
   appended under `## Additional sources consulted`.
2. Support check PER SECTION (never the whole report at once — 8k-ctx budget):
   one smart call per section with that section's notes bundle; unsupported
   sentences are rewritten ONLY when a note supports a fix, otherwise
   annotated `[unverified]` inline.
3. Contradiction check (deep preset only): one smart call per section verifies
   that conflicting notes are both presented.

The corrected report is rewritten to disk with plain open() (HP-022), and
`metrics.verification` is merged on `research_runs.metrics`.
"""

import json
import logging
import os
import re
from pathlib import Path

from research import events, llm, store, writer

logger = logging.getLogger(__name__)

# Mirror of main.DATA_DIR — importing `main` here is circular (same rationale
# as router.py / writer.py).
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

_CITE_RE = re.compile(r"\[(\d+)\]")
_HEADING_RE = re.compile(r"(?m)^## ")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])\n")

# Sections whose text is programmatic (never LLM-verified).
_PROGRAMMATIC_HEADINGS = {"Sources", "Additional sources consulted"}


def _split_sections(text: str) -> list[dict]:
    """Split the report into preamble + `## <heading>` sections, preserving
    the exact text so reassembly is lossless. Each dict: heading, body."""
    heads = [m.start() for m in _HEADING_RE.finditer(text)]
    if not heads:
        return [{"heading": "", "body": text}]
    sections: list[dict] = []
    if heads[0] > 0:
        sections.append({"heading": "", "body": text[: heads[0]]})
    for i, h in enumerate(heads):
        end = heads[i + 1] if i + 1 < len(heads) else len(text)
        chunk = text[h:end]
        nl = chunk.find("\n")
        heading = chunk[3:nl].strip() if nl != -1 else chunk[3:].strip()
        body = chunk[nl + 1 :] if nl != -1 else ""
        sections.append({"heading": heading, "body": body})
    return sections


def _fix_citations(section_text: str, valid: set[int]) -> tuple[str, set[int]]:
    """Strip `[n]` markers that have no source row; the sentence keeps its
    position but the claim is annotated `[unverified]`. Returns the corrected
    text and the set of citation numbers actually used."""

    def _rep(m: re.Match) -> str:
        n = int(m.group(1))
        if n in valid:
            cited.add(n)
            return m.group(0)
        return " [unverified]"

    cited: set[int] = set()
    return _CITE_RE.sub(_rep, section_text), cited


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def _apply_verdicts(section_text: str, verdicts: list[dict]) -> tuple[str, int, int]:
    """Apply LLM support-check verdicts: replace when a fix exists, else
    annotate `[unverified]`. Returns (text, unsupported, fixed)."""
    unsupported = 0
    fixed = 0
    for v in verdicts:
        sentence = str(v.get("sentence") or "").strip()
        if not sentence:
            continue
        fix = str(v.get("fix") or "").strip()
        if sentence in section_text:
            if fix:
                section_text = section_text.replace(sentence, fix, 1)
                fixed += 1
            else:
                section_text = section_text.replace(
                    sentence, f"{sentence} [unverified]", 1
                )
                unsupported += 1
        else:
            # The model failed to copy the sentence verbatim — cannot locate
            # it, so count it as unsupported and leave the text untouched.
            logger.debug("verify: flagged sentence not found in section: %r", sentence[:120])
            unsupported += 1
    return section_text, unsupported, fixed


async def _section_bundle(ctx: dict, heading: str) -> str:
    """Notes bundle for a section: prefer the outline mapping (writer stashed
    ctx['sections']), else fall back to every task's notes."""
    sections = ctx.get("sections") or []
    for s in sections:
        if s.get("title") == heading:
            return await writer.notes_bundle(ctx, s.get("task_idxs") or [])
    tasks = await store.get_tasks(str(ctx["run"]["id"]))
    return await writer.notes_bundle(ctx, [t["idx"] for t in tasks])


async def _verify_section(ctx: dict, heading: str, body: str) -> tuple[str, int, int]:
    """One smart call: body + its notes bundle -> verdict list. Returns
    (corrected body, unsupported, fixed). Never raises — on any failure the
    section is kept as-is."""
    smart = ctx["roles"].get("smart")
    if smart is None:
        logger.warning("verify: no smart role — skipping support check")
        return body, 0, 0
    bundle = await _section_bundle(ctx, heading)
    prompt = llm.load_prompt("verify.md")
    prompt = prompt.replace("{section}", body[:6000]).replace("{notes}", bundle)[:12000]
    try:
        result = await ctx["llm"].complete(
            smart[0],
            smart[1],
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Verify the section."},
            ],
        )
    except Exception as exc:
        logger.warning("verify: support check failed for %r: %s", heading, exc)
        return body, 0, 0
    parsed = ctx["llm"].extract_json(result["text"])
    verdicts = parsed.get("unsupported") if isinstance(parsed, dict) else None
    if not isinstance(verdicts, list):
        logger.debug("verify: unparseable verdicts for %r", heading)
        return body, 0, 0
    return _apply_verdicts(body, verdicts)


async def _contradiction_check(ctx: dict, heading: str, body: str) -> tuple[str, int]:
    """Deep preset only: one smart call per section — conflicting notes must
    both be presented. Appends a limitation line for each missed conflict.
    Returns (body, contradictions_found)."""
    smart = ctx["roles"].get("smart")
    if smart is None:
        return body, 0
    bundle = await _section_bundle(ctx, heading)
    prompt = llm.load_prompt("contradict.md")
    prompt = prompt.replace("{section}", body[:6000]).replace("{notes}", bundle)[:12000]
    try:
        result = await ctx["llm"].complete(
            smart[0],
            smart[1],
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Check for one-sided treatment of conflicts."},
            ],
        )
    except Exception as exc:
        logger.warning("verify: contradiction check failed for %r: %s", heading, exc)
        return body, 0
    parsed = ctx["llm"].extract_json(result["text"])
    issues = parsed.get("contradictions") if isinstance(parsed, dict) else None
    if not isinstance(issues, list):
        return body, 0
    notes: list[str] = []
    found = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("both_sides_presented") is False and issue.get("note"):
            note = str(issue["note"]).strip()[:300]
            if note:
                notes.append(note)
                found += 1
    if notes:
        body = body.rstrip() + "\n\n*(Limitation: " + "; ".join(notes) + ")*\n"
    return body, found


async def verify_report(ctx: dict, report_path: str) -> None:
    """Run the full VERIFY stage and rewrite the report file in place.

    Signature note: takes the report's relative path (writer persisted it on
    `research_runs.report_path`) and reads the file itself — the file is the
    single source of truth and rewriting it here avoids plumbing the ~12KB
    markdown string through the runner.
    """
    run_id = str(ctx["run"]["id"])
    abs_path = Path(DATA_DIR) / report_path
    if not abs_path.exists():
        logger.warning("verify: report file missing at %s", abs_path)
        await events.append(
            run_id, "verify", {"checked": 0, "unsupported": 0, "fixed": 0, "missing": True}
        )
        return

    sources = await store.get_run_sources(run_id)
    valid = {int(s["n"]) for s in sources}
    text = abs_path.read_text(encoding="utf-8")
    sections = _split_sections(text)

    checked = 0
    unsupported_total = 0
    fixed_total = 0
    contradictions = 0
    body_cited: set[int] = set()
    content_sections: list[dict] = []

    for sec in sections:
        if not sec["heading"]:
            content_sections.append(sec)
            continue
        # Programmatic citation hygiene runs on EVERY section (including the
        # numbered Sources list — every [n] in the file must resolve); the LLM
        # support check is limited to verifiable body sections, and only body
        # citations count towards "never cited" below.
        body, cited = _fix_citations(sec["body"], valid)
        if sec["heading"] in _PROGRAMMATIC_HEADINGS:
            content_sections.append({"heading": sec["heading"], "body": body})
            continue
        body_cited |= cited
        # Re-run citation hygiene AFTER LLM verdicts so a fix that invents a
        # citation number is also caught by the programmatic pass.
        body, unsupported, fixed = await _verify_section(ctx, sec["heading"], body)
        body, cited2 = _fix_citations(body, valid)
        body_cited |= cited2
        checked += len(_sentences(body))
        unsupported_total += unsupported
        fixed_total += fixed
        if ctx["run"].get("depth") == "deep":
            body, contradictions = await _contradiction_check(ctx, sec["heading"], body)
        content_sections.append({"heading": sec["heading"], "body": body})

    never_cited = sorted(valid - body_cited)
    if never_cited:
        lines = []
        for s in sources:
            if int(s["n"]) not in never_cited:
                continue
            title = s.get("title") or s.get("domain") or s["url"]
            lines.append(f"- [{s['n']}] {title} — {s.get('domain') or ''} — {s['url']}")
        content_sections.append(
            {"heading": "Additional sources consulted", "body": "\n" + "\n".join(lines) + "\n"}
        )

    rebuilt = []
    for sec in content_sections:
        prefix = f"## {sec['heading']}\n" if sec["heading"] else ""
        piece = prefix + sec["body"]
        if rebuilt and not rebuilt[-1].endswith("\n"):
            piece = "\n" + piece
        rebuilt.append(piece)
    corrected = "".join(rebuilt)

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(corrected)

    stats = {
        "checked": checked,
        "unsupported": unsupported_total,
        "fixed": fixed_total,
        "contradictions": contradictions,
    }
    row = await store.get_run(run_id)
    metrics = row["metrics"] if row else {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics or "{}")
    metrics["verification"] = stats
    await store.update_run(run_id, metrics=metrics, summary=corrected[:500])
    await events.append(
        run_id,
        "verify",
        {"checked": stats["checked"], "unsupported": stats["unsupported"], "fixed": stats["fixed"]},
    )
    logger.info("verify: run %s -> %s", run_id, stats)