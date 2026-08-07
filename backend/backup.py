import asyncio
import json
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["backup"])

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
EXPORTS_DIR = DATA_DIR / "exports"
DUMP_NAME = "db.dump"
PROVIDERS_NAME = "providers.json"
SUBPROCESS_TIMEOUT = 300
MAX_IMPORT_BYTES = 200 * 1024 * 1024

_restoring = False


def is_restoring() -> bool:
    return _restoring


async def _run(argv: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{Path(argv[0]).name} timed out after {SUBPROCESS_TIMEOUT}s")
    if proc.returncode != 0:
        tail = (stderr or b"").decode(errors="replace")[-400:].strip()
        raise RuntimeError(f"{Path(argv[0]).name} exited {proc.returncode}: {tail}")


async def _build_export(dest: Path, include_keys: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="export-work-") as workdir:
        tmp = Path(workdir)
        dump_path = tmp / DUMP_NAME
        argv = ["pg_dump", "--format=custom", f"--file={dump_path}"]
        if not include_keys:
            argv.append("--exclude-table-data=public.providers")
            argv.append("--exclude-table-data=public.provider_favorites")
            argv.append("--exclude-table-data=public.app_state")
        argv.append(os.environ["DATABASE_URL"])
        await _run(argv)

        providers_json = None
        if not include_keys:
            rows = await db.fetch(
                """
                SELECT id, name, base_url, type, kind, preset, is_default, created_at
                FROM providers
                ORDER BY id
                """
            )
            providers = []
            for row in rows:
                item = dict(row)
                if item["created_at"] is not None:
                    item["created_at"] = item["created_at"].isoformat()
                providers.append(item)
            fav_rows = await db.fetch(
                "SELECT provider_id, model_id, pinned_at FROM provider_favorites ORDER BY provider_id"
            )
            favorites = []
            for row in fav_rows:
                item = dict(row)
                if item["pinned_at"] is not None:
                    item["pinned_at"] = item["pinned_at"].isoformat()
                favorites.append(item)
            state_row = await db.fetchrow(
                "SELECT active_provider_id, active_model, rag_auto FROM app_state WHERE id = TRUE"
            )
            state = dict(state_row) if state_row else None
            providers_json = tmp / PROVIDERS_NAME
            providers_json.write_text(
                json.dumps(
                    {"providers": providers, "favorites": favorites, "state": state},
                    indent=2,
                ),
                encoding="utf-8",
            )

        with tarfile.open(dest, "w:gz") as tar:
            tar.add(dump_path, arcname=DUMP_NAME)
            if providers_json is not None:
                tar.add(providers_json, arcname=PROVIDERS_NAME)
            if DATA_DIR.exists():
                for path in sorted(DATA_DIR.rglob("*")):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(DATA_DIR)
                    if rel.parts and rel.parts[0] == "exports":
                        continue
                    tar.add(path, arcname=str(Path("data") / rel))


@router.get("/export")
async def export_backup(include_keys: bool = False) -> FileResponse:
    tmp = tempfile.TemporaryDirectory(prefix="export-")
    tmp_path = Path(tmp.name)
    archive = tmp_path / "export.tar.gz"
    try:
        await _build_export(archive, include_keys)
    except Exception as exc:
        tmp.cleanup()
        logger.error("export failed: %s", exc)
        raise HTTPException(500, f"export failed: {exc}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return FileResponse(
        archive,
        filename=f"local-ai-hub-export-{stamp}.tar.gz",
        background=BackgroundTask(tmp.cleanup),
    )


def _validate_members(tar: tarfile.TarFile) -> None:
    for member in tar.getmembers():
        name = member.name
        if (
            name.startswith("/")
            or ".." in Path(name).parts
            or member.issym()
            or member.islnk()
        ):
            raise HTTPException(400, "unsafe archive")


async def _restore_providers(work: Path) -> None:
    providers_json = work / PROVIDERS_NAME
    if not providers_json.exists():
        return
    payload = json.loads(providers_json.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = {"providers": payload, "favorites": []}
    for row in payload.get("providers", []):
        created_at = row.get("created_at")
        await db.execute(
            """
            INSERT INTO providers (id, name, base_url, type, kind, preset, is_default, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO NOTHING
            """,
            row["id"],
            row["name"],
            row["base_url"],
            row.get("type", "openai"),
            row.get("kind", "local"),
            row.get("preset"),
            row.get("is_default", False),
            datetime.fromisoformat(created_at) if created_at else None,
        )
    await db.execute(
        "SELECT setval('providers_id_seq', (SELECT COALESCE(max(id), 1) FROM providers))"
    )
    for row in payload.get("favorites", []):
        pinned_at = row.get("pinned_at")
        await db.execute(
            """
            INSERT INTO provider_favorites (provider_id, model_id, pinned_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (provider_id, model_id) DO NOTHING
            """,
            row["provider_id"],
            row["model_id"],
            datetime.fromisoformat(pinned_at) if pinned_at else None,
        )
    state = payload.get("state")
    if state:
        active_provider_id = state.get("active_provider_id")
        if active_provider_id is not None:
            exists = await db.fetchval(
                "SELECT 1 FROM providers WHERE id = $1", active_provider_id
            )
            if not exists:
                active_provider_id = None
        await db.execute(
            """
            INSERT INTO app_state (id, active_provider_id, active_model, rag_auto)
            VALUES (TRUE, $1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                active_provider_id = EXCLUDED.active_provider_id,
                active_model = EXCLUDED.active_model,
                rag_auto = EXCLUDED.rag_auto
            """,
            active_provider_id,
            state.get("active_model"),
            state.get("rag_auto", True),
        )


def _restore_data_tree(work: Path) -> None:
    data_tree = work / "data"
    if not data_tree.exists():
        return
    for path in sorted(data_tree.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_tree)
        dest = DATA_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)


@router.post("/import")
async def import_backup(file: UploadFile, confirm: bool = False) -> dict:
    global _restoring
    if not confirm:
        raise HTTPException(400, "pass confirm=true to proceed")

    tmp = tempfile.TemporaryDirectory(prefix="import-")
    tmp_path = Path(tmp.name)
    archive = tmp_path / "upload.tar.gz"
    work = tmp_path / "work"
    work.mkdir()
    try:
        size = 0
        with archive.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(413, "archive too large (200 MB max)")
                fh.write(chunk)
        try:
            tar = tarfile.open(archive, "r:gz")
        except tarfile.TarError:
            raise HTTPException(400, "not a valid .tar.gz archive")
        with tar:
            _validate_members(tar)
            if DUMP_NAME not in {m.name for m in tar.getmembers()}:
                raise HTTPException(400, "archive missing db.dump")
            tar.extractall(work, filter="data")

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        snapshot_name = f"pre-import-{stamp}.tar.gz"
        await _build_export(EXPORTS_DIR / snapshot_name, include_keys=True)

        _restoring = True
        try:
            live_keys = await db.fetch(
                "SELECT id, api_key_enc, key_hint FROM providers WHERE api_key_enc IS NOT NULL"
            )
            await _run(
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    f"--dbname={os.environ['DATABASE_URL']}",
                    str(work / DUMP_NAME),
                ]
            )
            await _restore_providers(work)
            for row in live_keys:
                await db.execute(
                    """
                    UPDATE providers SET api_key_enc = $2, key_hint = $3
                    WHERE id = $1 AND api_key_enc IS NULL
                    """,
                    row["id"],
                    row["api_key_enc"],
                    row["key_hint"],
                )
            _restore_data_tree(work)
        finally:
            _restoring = False
        return {"ok": True, "snapshot": snapshot_name, "restart_required": True}
    finally:
        tmp.cleanup()
