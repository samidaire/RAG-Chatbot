import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.services.ingestion import process_document
from app.utils.chunking import stable_document_id
from app.db.mongo import documents_col

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("")
async def ingest(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    chunk_size: int = Form(1000),
    overlap: int = Form(150),
):
    if not file and not text:
        raise HTTPException(status_code=400, detail={"error": "missing_input", "message": "Provide either file or text"})
    if file and text:
        raise HTTPException(status_code=400, detail={"error": "ambiguous_input", "message": "Provide only one of file or text"})

    raw_bytes: bytes
    source_name: str
    if file:
        data = await file.read()
        raw_bytes = data
        source_name = file.filename
    else:
        raw_bytes = text.encode("utf-8")  # type: ignore[arg-type]
        source_name = "text-input"

    doc_hash = stable_document_id(raw_bytes)
    docs = documents_col()
    existing = await docs.find_one({"_id": doc_hash})
    if existing:
        return {"status": "exists", "document_id": doc_hash, "chunks": existing.get("num_chunks", 0)}

    is_pdf = bool(file and file.filename.lower().endswith(".pdf"))
    doc_id, count = await process_document(raw_bytes, source_name, is_pdf=is_pdf, chunk_size=chunk_size, overlap=overlap)
    return {"status": "ingested", "document_id": doc_id, "chunks": count}
