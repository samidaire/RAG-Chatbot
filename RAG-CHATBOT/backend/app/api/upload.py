import uuid
import logging
from datetime import datetime
from typing import List, Dict, Any
import os
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, HTTPException, Query, Form, Body

from app.db.mongo import documents_col
from app.utils.chunking import stable_document_id
from app.core.settings import get_settings
from app.db.mongo import upload_jobs_col
from app.services.ingestion import process_document
from app.services.s3 import upload_bytes, is_s3_enabled

logger = logging.getLogger(__name__)

router = APIRouter(tags=["upload"])  # root-level endpoints

# Simple in-memory job store (MVP). For production, replace with Redis / DB.
_jobs: Dict[str, Dict[str, Any]] = {}

RAW_BYTES_INLINE_LIMIT = 1_500_000  # 1.5MB safety cap for base64 inline storage

async def _persist_job(job: Dict[str, Any]) -> None:
    col = upload_jobs_col()
    doc = {**job, "_id": job["job_id"]}
    # Avoid storing in-memory raw bytes directly; convert to base64 if present and small
    for d in doc.get("documents", []):
        rb = d.get("raw_bytes")
        if isinstance(rb, (bytes, bytearray)):
            if len(rb) <= RAW_BYTES_INLINE_LIMIT:
                import base64
                d["raw_b64"] = base64.b64encode(rb).decode("utf-8")
            d.pop("raw_bytes", None)
    await col.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)

async def _load_job(job_id: str) -> Dict[str, Any] | None:
    job = _jobs.get(job_id)
    if job:
        return job
    col = upload_jobs_col()
    db_doc = await col.find_one({"_id": job_id})
    if not db_doc:
        return None
    # Rehydrate raw_bytes from raw_b64 if present
    for d in db_doc.get("documents", []):
        rb64 = d.get("raw_b64")
        if rb64:
            import base64
            try:
                d["raw_bytes"] = base64.b64decode(rb64)
            except Exception:  # noqa: BLE001
                d["raw_bytes"] = None
    # Put into memory cache (shallow copy ok)
    job_obj = dict(db_doc)
    job_obj.pop("_id", None)
    _jobs[job_id] = job_obj
    return job_obj

def _new_job(status: str, total: int = 0) -> str:
    jid = str(uuid.uuid4())
    _jobs[jid] = {
        "job_id": jid,
        "status": status,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "total_files": total,
        "processed_files": 0,
        "errors": [],
        "documents": [],
    }
    return jid

def _update_job(jid: str, **fields):
    job = _jobs.get(jid)
    if not job:
        return
    job.update(fields)
    job["updated_at"] = datetime.utcnow()


@router.post("/upload-files")
async def upload_files(
    background: BackgroundTasks,
    files: List[UploadFile] = File(...),
    auto_ingest: bool = Query(True, description="If true, automatically run ingestion after upload finishes"),
    chunk_size: int = Query(500, description="Chunk size (only if auto_ingest)"),
    overlap: int = Query(50, description="Chunk overlap (only if auto_ingest)"),
    propagate_s3_key: bool = Query(True, description="If true, copy uploaded file's s3_key into document record when store_original is False"),
    backfill_on_exists: bool = Query(True, description="If a document already exists but has no s3_key, set it from upload metadata"),
):
    if not files:
        raise HTTPException(status_code=400, detail={"error": "no_files", "message": "No files uploaded"})
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail={"error": "invalid_type", "message": f"Non-PDF file: {f.filename}"})

    job_id = _new_job("queued", total=len(files))
    settings = get_settings()

    # Pre-read all files BEFORE returning so file handles are not closed when background executes
    file_entries: list[dict[str, object]] = []
    for f in files:
        try:
            data = await f.read()  # read while UploadFile still open
            file_entries.append({"filename": f.filename, "data": data})
        except Exception as e:  # noqa: BLE001
            _jobs[job_id]["errors"].append({"file": f.filename, "error": str(e)})

    async def process():  # noqa: D401
        for entry in file_entries:
            filename = entry["filename"]  # type: ignore[index]
            data = entry["data"]  # type: ignore[index]
            try:
                assert isinstance(data, (bytes, bytearray))
                doc_hash = stable_document_id(data)
                existing = await documents_col().find_one({"_id": doc_hash})
                file_name_safe = str(filename).replace(os.sep, "_")
                s3_key = f"documents/{doc_hash}__{file_name_safe}"
                raw_bytes: bytes | None = None
                if is_s3_enabled() and not existing:
                    try:
                        upload_bytes(data, s3_key)
                    except HTTPException as he:  # capture S3 config / upload errors
                        logger.error("S3 upload failed for %s: %s", filename, he.detail)
                        _jobs[job_id]["errors"].append({"file": filename, "error": he.detail})
                        _jobs[job_id]["processed_files"] += 1
                        continue
                elif settings.s3_bucket and not is_s3_enabled():
                    logger.warning("S3_BUCKET set but credentials incomplete; skipping S3 upload for %s", filename)
                    _jobs[job_id]["errors"].append({"file": filename, "error": "s3_incomplete_config"})
                    _jobs[job_id]["processed_files"] += 1
                    continue
                elif not settings.s3_bucket:
                    raw_bytes = data
                status = "exists" if existing else "uploaded"
                _jobs[job_id]["documents"].append({
                    "document_id": doc_hash,
                    "status": status,
                    "s3_key": s3_key if settings.s3_bucket else None,
                    "filename": filename,
                    "size": len(data),
                    "raw_bytes": raw_bytes,
                })
                _jobs[job_id]["processed_files"] += 1
            except Exception as e:  # noqa: BLE001
                _jobs[job_id]["errors"].append({"file": filename, "error": str(e)})
                _jobs[job_id]["processed_files"] += 1
        # Mark uploaded prior to optional auto ingest
        _update_job(job_id, status="uploaded")
        await _persist_job(_jobs[job_id])

        if auto_ingest:
            # Transition to processing directly
            _update_job(job_id, status="processing", processed_count=0, ingested=0)
            await _persist_job(_jobs[job_id])

            async def run_auto():
                from app.services.ingestion import process_document
                from app.db.mongo import documents_col
                ingested = 0
                docs_meta = _jobs[job_id].get("documents", [])
                for meta in docs_meta:
                    try:
                        if meta.get("status") == "exists":
                            # Backfill s3_key if requested and missing on doc
                            if backfill_on_exists and propagate_s3_key and meta.get("s3_key"):
                                try:
                                    await documents_col().update_one(
                                        {"_id": meta.get("document_id") or stable_document_id(meta.get("raw_bytes") or b""), "$or": [{"s3_key": {"$exists": False}}, {"s3_key": None}]},
                                        {"$set": {"s3_key": meta.get("s3_key")}},
                                    )
                                    meta["s3_key_propagated"] = True
                                except Exception:  # noqa: BLE001
                                    meta["s3_key_propagated"] = False
                            continue
                        s3_key = meta.get("s3_key")
                        if s3_key and get_settings().s3_bucket:
                            from app.services.s3 import get_object_bytes
                            data2 = get_object_bytes(s3_key)
                        else:
                            data2 = meta.get("raw_bytes")
                            if not data2:
                                meta["status"] = "missing_file"
                                continue
                        # Set document status to processing before starting
                        doc_id = meta.get("document_id") or stable_document_id(data2)
                        await documents_col().update_one(
                            {"_id": doc_id},
                            {"$set": {"processing_status": "processing", "updated_at": datetime.utcnow(), "last_error": None}}
                        )
                        is_pdf = meta.get("filename", "").lower().endswith(".pdf")
                        doc_id, count = await process_document(
                            data2,
                            meta.get("filename", "unknown"),
                            is_pdf=is_pdf,
                            chunk_size=chunk_size,
                            overlap=overlap,
                            store_original=False,
                        )
                        meta["document_id"] = doc_id
                        meta["chunks"] = count
                        if meta["status"] != "exists":
                            meta["status"] = "ingested"
                            ingested += 1
                        # Propagate s3_key if original not stored by process_document
                        if propagate_s3_key and meta.get("s3_key"):
                            try:
                                await documents_col().update_one(
                                    {"_id": doc_id, "$or": [{"s3_key": {"$exists": False}}, {"s3_key": None}]},
                                    {"$set": {"s3_key": meta.get("s3_key")}},
                                )
                                meta["s3_key_propagated"] = True
                            except Exception:  # noqa: BLE001
                                meta["s3_key_propagated"] = False
                    except HTTPException as he:  # pass HTTP errors
                        meta["status"] = "error"
                        meta["error"] = he.detail
                        # Update document status to failed in database
                        doc_id = meta.get("document_id") or stable_document_id(meta.get("raw_bytes") or b"")
                        if doc_id:
                            await documents_col().update_one(
                                {"_id": doc_id},
                                {"$set": {"processing_status": "failed", "last_error": str(he.detail), "updated_at": datetime.utcnow()}}
                            )
                    except Exception as e:  # noqa: BLE001
                        meta["status"] = "error"
                        meta["error"] = str(e)
                        # Update document status to failed in database
                        doc_id = meta.get("document_id") or stable_document_id(meta.get("raw_bytes") or b"")
                        if doc_id:
                            await documents_col().update_one(
                                {"_id": doc_id},
                                {"$set": {"processing_status": "failed", "last_error": str(e), "updated_at": datetime.utcnow()}}
                            )
                _update_job(job_id, status="completed", ingested=ingested)
                await _persist_job(_jobs[job_id])

            background.add_task(run_auto)

    # Persist initial shell
    await _persist_job(_jobs[job_id])
    background.add_task(process)
    return {"job_id": job_id, "status": "queued", "auto_ingest": auto_ingest, "propagate_s3_key": propagate_s3_key}


 # removed alias: primary path now /upload-files


@router.get("/upload-status/{job_id}")
async def upload_status(job_id: str):
    job = await _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "job_not_found", "message": "Unknown job id"})
    # Derive error summary fields on the fly
    errors = job.get("errors", []) or []
    job["error_count"] = len(errors)
    if errors:
        first = errors[0]
        job["first_error"] = first.get("error") if isinstance(first, dict) else first
    else:
        job["first_error"] = None
    return job


@router.post("/process-documents")
async def process_documents(
    background: BackgroundTasks,
    job_id: str | None = Query(default=None, description="Upload job id"),
    job_id_form: str | None = Form(default=None),
    payload: dict | None = Body(default=None),
    chunk_size: int = Query(500),
    overlap: int = Query(150),
    propagate_s3_key: bool = Query(True, description="Propagate upload s3_key into document record if missing"),
    backfill_on_exists: bool = Query(True, description="Backfill s3_key for already existing docs missing it"),
):
    # Accept job_id from query > form > JSON body
    if job_id is None:
        if job_id_form is not None:
            job_id = job_id_form
        elif payload and isinstance(payload, dict):
            job_id = payload.get("job_id")  # type: ignore[assignment]
    if not job_id:
        raise HTTPException(status_code=422, detail={"error": "job_id_missing", "message": "Provide job_id via query, form, or JSON body"})

    job = await _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error": "job_not_found", "message": "Unknown job id"})
    if job["status"] == "completed":
        # Idempotent return: compute ingested count from documents
        ingested = sum(1 for d in job.get("documents", []) if d.get("status") == "ingested")
        return {"job_id": job_id, "status": "completed", "ingested": ingested}
    if job["status"] not in ("uploaded", "queued"):
        raise HTTPException(status_code=400, detail={"error": "bad_status", "message": f"Job not in uploaded state: {job['status']}"})

    docs_meta = job.get("documents", [])
    if not docs_meta:
        raise HTTPException(status_code=400, detail={"error": "no_documents", "message": "No staged documents to process"})

    _update_job(job_id, status="processing", processed_count=0, ingested=0)

    async def run():
        ingested = 0
        for meta in docs_meta:
            try:
                if meta.get("status") == "exists":
                    if backfill_on_exists and propagate_s3_key and meta.get("s3_key"):
                        from app.db.mongo import documents_col
                        try:
                            await documents_col().update_one(
                                {"_id": meta.get("document_id") or stable_document_id(meta.get("raw_bytes") or b""), "$or": [{"s3_key": {"$exists": False}}, {"s3_key": None}]},
                                {"$set": {"s3_key": meta.get("s3_key")}},
                            )
                            meta["s3_key_propagated"] = True
                        except Exception:  # noqa: BLE001
                            meta["s3_key_propagated"] = False
                    continue
                s3_key = meta.get("s3_key")
                if s3_key and get_settings().s3_bucket:
                    from app.services.s3 import get_object_bytes
                    data = get_object_bytes(s3_key)
                else:
                    data = meta.get("raw_bytes")
                    if not data:
                        meta["status"] = "missing_file"
                        continue
                is_pdf = meta.get("filename", "").lower().endswith(".pdf")
                doc_id, count = await process_document(
                    data,
                    meta.get("filename", "unknown"),
                    is_pdf=is_pdf,
                    chunk_size=chunk_size,
                    overlap=overlap,
                    store_original=False,
                )
                meta["document_id"] = doc_id
                meta["chunks"] = count
                if meta["status"] != "exists":
                    meta["status"] = "ingested"
                    ingested += 1
                if propagate_s3_key and meta.get("s3_key"):
                    from app.db.mongo import documents_col
                    try:
                        await documents_col().update_one(
                            {"_id": doc_id, "$or": [{"s3_key": {"$exists": False}}, {"s3_key": None}]},
                            {"$set": {"s3_key": meta.get("s3_key")}},
                        )
                        meta["s3_key_propagated"] = True
                    except Exception:  # noqa: BLE001
                        meta["s3_key_propagated"] = False
            except HTTPException as he:
                meta["status"] = "error"
                meta["error"] = he.detail
            except Exception as e:  # noqa: BLE001
                meta["status"] = "error"
                meta["error"] = str(e)
        _update_job(job_id, status="completed", ingested=ingested)
        await _persist_job(_jobs[job_id])
    background.add_task(run)
    return {"job_id": job_id, "status": "processing"}
