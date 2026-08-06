import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

import db
from .ingest import kickoff_ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["rag"])

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ALLOWED_EXTS = {".pdf", ".md", ".txt"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    filename = file.filename or "unnamed"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTS))}",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 50MB)")

    rel_dir = Path("docs") / datetime.utcnow().strftime("%Y-%m")
    save_dir = DATA_DIR / rel_dir
    save_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4()}{ext}"
    save_path = save_dir / unique_name
    save_path.write_bytes(data)

    row = await db.fetchrow(
        "INSERT INTO documents (filename, path) VALUES ($1, $2) RETURNING id",
        filename,
        str(save_path),
    )
    doc_id = row["id"]
    kickoff_ingest(doc_id)
    return {"id": doc_id, "status": "pending"}


@router.get("/documents")
async def list_documents() -> list[dict]:
    rows = await db.fetch(
        """
        SELECT id, filename, status, chunk_count, error, created_at
        FROM documents
        ORDER BY id DESC
        """
    )
    return [dict(row) for row in rows]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int) -> dict:
    row = await db.fetchrow(
        """
        SELECT id, filename, path, status, chunk_count, error, created_at
        FROM documents WHERE id = $1
        """,
        doc_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")

    chunks = await db.fetch(
        """
        SELECT idx, page, left(content, 200) AS preview
        FROM chunks
        WHERE document_id = $1
        ORDER BY idx
        LIMIT 3
        """,
        doc_id,
    )
    result = dict(row)
    result["chunks_preview"] = [dict(c) for c in chunks]
    return result


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int) -> dict:
    row = await db.fetchrow(
        "SELECT path FROM documents WHERE id = $1", doc_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")

    await db.execute("DELETE FROM documents WHERE id = $1", doc_id)
    path = row["path"]
    if path:
        Path(path).unlink(missing_ok=True)
    return {"ok": True}


@router.post("/documents/{doc_id}/reingest")
async def reingest_document(doc_id: int) -> dict:
    row = await db.fetchrow("SELECT id FROM documents WHERE id = $1", doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")

    await db.execute(
        "UPDATE documents SET status = 'pending', error = NULL WHERE id = $1",
        doc_id,
    )
    kickoff_ingest(doc_id)
    return {"id": doc_id, "status": "pending"}


@router.post("/rag/toggle_auto")
async def toggle_auto() -> dict:
    row = await db.fetchrow(
        "UPDATE app_state SET rag_auto = NOT rag_auto RETURNING rag_auto"
    )
    return {"rag_auto": bool(row["rag_auto"])}
