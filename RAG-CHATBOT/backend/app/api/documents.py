import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Query
from typing import List
from datetime import datetime
from app.db.mongo import documents_col, chunks_col
from app.services.pinecone_client import delete_document_vectors
from app.services.s3 import is_s3_enabled, delete_object, upload_bytes
from app.services.ingestion import process_document
from app.utils.chunking import stable_document_id
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
async def list_documents(limit: int = 100):
    cursor = documents_col().find({}, {"_id": 1, "source_name": 1, "created_at": 1, "num_chunks": 1, "s3_key": 1, "processing_status": 1, "updated_at": 1}).sort("created_at", -1).limit(limit)
    docs = []
    async for d in cursor:
        docs.append({
            "document_id": d["_id"],
            "source_name": d.get("source_name"),
            "created_at": d.get("created_at"),
            "num_chunks": d.get("num_chunks"),
            "has_s3": bool(d.get("s3_key")),
            "processing_status": d.get("processing_status"),
            "updated_at": d.get("updated_at"),
        })
    return {"documents": docs}


@router.post("/upload")
async def upload_documents(
    background: BackgroundTasks,
    files: List[UploadFile] = File(...),
    auto_process: bool = Query(True),
):
    if not files:
        raise HTTPException(status_code=400, detail={"error": "no_files"})
    settings = get_settings()
    created: list[dict] = []
    for f in files:
        if not f.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail={"error": "invalid_type", "message": f"Only PDF accepted: {f.filename}"})
        data = await f.read()
        doc_hash = stable_document_id(data)
        s3_key = f"documents/{doc_hash}__{f.filename.replace('/', '_')}"
        existing = await documents_col().find_one({"_id": doc_hash})
        if not existing:
            # Upload file bytes (single canonical object keyed by content hash + filename)
            if is_s3_enabled():
                upload_bytes(data, s3_key)
            now = datetime.utcnow()
            await documents_col().insert_one({
                "_id": doc_hash,
                "source_name": f.filename,
                "s3_key": s3_key if is_s3_enabled() else None,
                "processing_status": "uploaded",
                "created_at": now,
                "updated_at": now,
                "num_chunks": 0,
                "last_error": None,
            })
        else:
            # Reuse existing doc (content identical if hash matches) - just refresh timestamps & optionally s3 key
            await documents_col().update_one({"_id": doc_hash}, {"$set": {"updated_at": datetime.utcnow(), "processing_status": "uploaded", "last_error": None, "s3_key": existing.get("s3_key") or (s3_key if is_s3_enabled() else None)}})
        created.append({"document_id": doc_hash, "filename": f.filename})

    if auto_process:
        async def run_process(doc_ids: list[str]):
            for doc_id in doc_ids:
                # Reload doc to get s3_key or detect missing
                doc = await documents_col().find_one({"_id": doc_id})
                if not doc:
                    continue
                await documents_col().update_one({"_id": doc_id}, {"$set": {"processing_status": "processing", "updated_at": datetime.utcnow()}})
                try:
                    # Fetch original bytes: prefer S3; if not present we cannot proceed
                    from app.services.s3 import get_object_bytes
                    s3_key_local = doc.get("s3_key")
                    if s3_key_local and is_s3_enabled():
                        data_bytes = get_object_bytes(s3_key_local)
                    else:
                        raise RuntimeError("original_bytes_missing")
                    await process_document(data_bytes, doc.get("source_name") or "unknown", is_pdf=doc.get("source_name"," ").lower().endswith('.pdf'), store_original=False, force_reprocess=True)
                except Exception as e:  # noqa: BLE001
                    await documents_col().update_one({"_id": doc_id}, {"$set": {"processing_status": "failed", "last_error": str(e), "updated_at": datetime.utcnow()}})
                else:
                    await documents_col().update_one({"_id": doc_id}, {"$set": {"processing_status": "completed", "updated_at": datetime.utcnow()}})
        background.add_task(run_process, [c["document_id"] for c in created])

    return {"uploaded": created, "auto_process": auto_process}


@router.post("/{document_id}/process")
async def process_single_document(document_id: str):
    doc = await documents_col().find_one({"_id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "document_not_found"})
    if doc.get("processing_status") == "processing":
        return {"document_id": document_id, "processing_status": "processing"}
    from app.services.s3 import get_object_bytes
    await documents_col().update_one({"_id": document_id}, {"$set": {"processing_status": "processing", "updated_at": datetime.utcnow(), "last_error": None}})
    try:
        s3_key = doc.get("s3_key")
        if not s3_key:
            raise HTTPException(status_code=400, detail={"error": "missing_s3_key"})
        data_bytes = get_object_bytes(s3_key)
        await process_document(data_bytes, doc.get("source_name") or "unknown", is_pdf=(doc.get("source_name"," ").lower().endswith('.pdf')), store_original=False, force_reprocess=True)
    except HTTPException as he:
        await documents_col().update_one({"_id": document_id}, {"$set": {"processing_status": "failed", "last_error": he.detail, "updated_at": datetime.utcnow()}})
        raise
    except Exception as e:  # noqa: BLE001
        await documents_col().update_one({"_id": document_id}, {"$set": {"processing_status": "failed", "last_error": str(e), "updated_at": datetime.utcnow()}})
        raise HTTPException(status_code=500, detail={"error": "processing_error", "message": str(e)})
    else:
        await documents_col().update_one({"_id": document_id}, {"$set": {"processing_status": "completed", "updated_at": datetime.utcnow()}})
    return {"document_id": document_id, "processing_status": "completed"}


@router.get("/{document_id}/status")
async def document_status(document_id: str):
    doc = await documents_col().find_one({"_id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "document_not_found"})
    return {
        "document_id": document_id,
        "processing_status": doc.get("processing_status"),
        "num_chunks": doc.get("num_chunks"),
        "last_error": doc.get("last_error"),
        "updated_at": doc.get("updated_at"),
        "created_at": doc.get("created_at"),
    }


@router.delete("/{document_id}")
async def delete_document(document_id: str, purge_s3: bool = False, strict_s3: bool = True):
    doc = await documents_col().find_one({"_id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "document_not_found", "message": "Unknown document id"})

    s3_key = doc.get("s3_key")
    s3_deleted = False
    s3_deleted_count: int | None = None
    s3_skipped_reason: str | None = None
    s3_available = is_s3_enabled()

    # S3 first
    if s3_available:
        if s3_key:
            try:
                delete_object(s3_key)
                s3_deleted = True
            except HTTPException as he:
                if strict_s3:
                    raise HTTPException(status_code=502, detail={"error": "s3_delete_failed", "message": "Primary S3 object deletion failed", "document_id": document_id, "detail": he.detail})
                s3_skipped_reason = "s3_object_delete_failed"
        else:
            if strict_s3:
                raise HTTPException(status_code=500, detail={"error": "s3_key_missing", "message": "Document missing s3_key; cannot strictly guarantee S3 deletion", "document_id": document_id})
            s3_skipped_reason = "no_s3_key"
        if purge_s3:
            from app.services.s3 import delete_prefix
            prefix = s3_key.rsplit('/', 1)[0] + '/' if s3_key and '/' in s3_key else f"{document_id}/"
            try:
                deleted_ct = delete_prefix(prefix)
                s3_deleted_count = (1 if s3_deleted else 0) + deleted_ct
            except HTTPException:
                if strict_s3:
                    raise HTTPException(status_code=502, detail={"error": "s3_prefix_delete_failed", "message": "Failed to purge S3 prefix", "prefix": prefix, "document_id": document_id})
                s3_skipped_reason = (s3_skipped_reason + ";prefix_delete_failed") if s3_skipped_reason else "prefix_delete_failed"
    else:
        if strict_s3:
            raise HTTPException(status_code=503, detail={"error": "s3_not_enabled", "message": "S3 not enabled but strict deletion requested"})
        s3_skipped_reason = "s3_not_enabled"

    # Delete vectors best effort
    try:
        delete_document_vectors(document_id)
    except Exception:  # noqa: BLE001
        pass

    # Delete chunks
    chunk_result = await chunks_col().delete_many({"document_id": document_id})

    # Delete doc
    await documents_col().delete_one({"_id": document_id})

    resp = {
        "document_id": document_id,
        "chunks_deleted": chunk_result.deleted_count,
        "s3_deleted": s3_deleted,
        "status": "deleted",
        "purge_s3": purge_s3,
        "strict_s3": strict_s3,
    }
    if s3_deleted_count is not None:
        resp["s3_deleted_count"] = s3_deleted_count
    if s3_skipped_reason:
        resp["s3_skipped_reason"] = s3_skipped_reason
    return resp


@router.get("/{document_id}")
async def get_document(document_id: str, include_internal: bool = False):
    doc = await documents_col().find_one({"_id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "document_not_found", "message": "Unknown document id"})
    base = {
        "document_id": doc["_id"],
        "source_name": doc.get("source_name"),
        "created_at": doc.get("created_at"),
        "num_chunks": doc.get("num_chunks"),
        "processing_status": doc.get("processing_status"),
        "updated_at": doc.get("updated_at"),
    }
    if include_internal:
        base["s3_key"] = doc.get("s3_key")
    return base