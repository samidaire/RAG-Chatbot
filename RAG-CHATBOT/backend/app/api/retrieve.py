import logging
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.settings import get_settings
from app.services.embedding import embed_texts
from app.services.pinecone_client import query_top_k
from app.db.mongo import chunks_col

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieve"])


class RetrieveRequest(BaseModel):
    query: str
    top_k: int | None = None


@router.post("")
async def retrieve(req: RetrieveRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail={"error": "empty_query", "message": "Query is empty"})
    settings = get_settings()
    top_k = req.top_k or settings.top_k
    if top_k <= 0 or top_k > 50:
        raise HTTPException(status_code=400, detail={"error": "invalid_top_k", "message": "top_k must be between 1 and 50"})

    embeds = await embed_texts([req.query])
    if not embeds:
        raise HTTPException(status_code=500, detail={"error": "embed_failed", "message": "Failed to embed query"})
    query_vec = embeds[0]

    try:
        matches = query_top_k(query_vec, top_k=top_k)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Pinecone query failed")
        raise HTTPException(status_code=502, detail={"error": "pinecone_query_failed", "message": str(e)}) from e

    chunk_ids: List[str] = [m["id"] for m in matches]
    if not chunk_ids:
        return {"chunks": [], "matches": []}

    # Fetch chunk docs
    cursor = chunks_col().find({"_id": {"$in": chunk_ids}})
    docs = {doc["_id"]: doc async for doc in cursor}

    ordered = []
    for m in matches:
        cid = m["id"]
        meta = m.get("metadata", {})
        chunk_doc = docs.get(cid)
        if not chunk_doc:
            continue
        ordered.append({
            "chunk_id": cid,
            "document_id": chunk_doc["document_id"],
            "score": m.get("score"),
            "text": chunk_doc["text"],
            "index": chunk_doc["index"],
            "metadata": meta,
        })

    return {"chunks": ordered, "count": len(ordered)}
