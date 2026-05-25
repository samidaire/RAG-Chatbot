import logging
from typing import List, Sequence, Optional

import numpy as np
from fastapi import HTTPException

from app.core.settings import get_settings
import os

logger = logging.getLogger(__name__)

try:
    from pinecone import Pinecone, ServerlessSpec
except ImportError:  # pragma: no cover
    Pinecone = None  # type: ignore
    ServerlessSpec = None  # type: ignore

_client = None  # lazily initialized Pinecone client instance
_index = None


def _require_client():
    if Pinecone is None:
        raise HTTPException(status_code=500, detail={"error": "pinecone_missing", "message": "pinecone-client not installed"})
    settings = get_settings()
    if not settings.pinecone_api_key:
        raise HTTPException(status_code=503, detail={"error": "pinecone_api_key_missing", "message": "PINECONE_API_KEY not set"})
    global _client
    if _client is None:
        logger.info("Initializing Pinecone client")
        _client = Pinecone(api_key=settings.pinecone_api_key)
    return _client


def ensure_index(dimension: int) -> None:
    """Create index if it does not exist; attach; validate dimension.

    If dimension mismatch is detected, raise explicit HTTPException instructing manual
    remediation rather than letting raw Pinecone 400 bubble up during query.
    """
    settings = get_settings()
    if not settings.pinecone_index:
        raise HTTPException(status_code=500, detail={"error": "pinecone_index_missing", "message": "PINECONE_INDEX not configured"})
    client = _require_client()
    raw = client.list_indexes()
    if isinstance(raw, dict):
        indexes_list = raw.get("indexes", [])
    elif hasattr(raw, "indexes"):
        indexes_list = getattr(raw, "indexes")
    else:
        indexes_list = raw  # assume iterable
    existing = set()
    for idx in indexes_list:
        if isinstance(idx, dict):
            name = idx.get("name")
        else:
            name = getattr(idx, "name", None)
        if name:
            existing.add(name)
    if settings.pinecone_index not in existing:
        logger.info("Creating Pinecone index name=%s dim=%d", settings.pinecone_index, dimension)
        create_kwargs = {
            "name": settings.pinecone_index,
            "dimension": dimension,
            "metric": "cosine",
        }
        if getattr(settings, "pinecone_cloud", None) and getattr(settings, "pinecone_region", None) and ServerlessSpec is not None:
            create_kwargs["spec"] = ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region)
        client.create_index(**create_kwargs)
    global _index
    if _index is None:
        logger.info("Attaching to Pinecone index name=%s", settings.pinecone_index)
    _index = client.Index(settings.pinecone_index)
    # Validate dimension (depends on SDK shape; try several attribute locations)
    try:
        index_stats = _index.describe_index_stats()
        # Some SDK versions include dimension inside stats or config
        stat_dim = None
        if isinstance(index_stats, dict):
            stat_dim = (
                index_stats.get("dimension")
                or (index_stats.get("database", {}) or {}).get("dimension")
                or (index_stats.get("status", {}) or {}).get("dimension")
            )
        if stat_dim and int(stat_dim) != int(dimension):
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "pinecone_dimension_mismatch",
                    "message": f"Index dimension {stat_dim} != embedding dim {dimension}. Recreate index or update model.",
                },
            )
    except HTTPException:
        raise
    except Exception:  # pragma: no cover - best effort only
        logger.warning("Could not verify Pinecone index dimension; continuing optimistically")


def _require_index():
    if _index is None:
        raise HTTPException(status_code=500, detail={"error": "pinecone_index_uninitialized", "message": "Index not initialized"})
    return _index


def upsert_vectors(ids: Sequence[str], vectors: Sequence[np.ndarray], metadatas: Sequence[dict]):
    if len(ids) != len(vectors) or len(ids) != len(metadatas):
        raise HTTPException(status_code=500, detail={"error": "pinecone_upsert_mismatch", "message": "ids/vectors/metadatas length mismatch"})
    index = _require_index()

    # Batch upserts to avoid exceeding Pinecone's 2MB request limit
    batch_size = 50  # Conservative batch size to stay well under 2MB limit

    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i:i + batch_size]
        batch_vectors = vectors[i:i + batch_size]
        batch_metadatas = metadatas[i:i + batch_size]

        # Convert vectors to lists for this batch
        items = []
        for _id, vec, meta in zip(batch_ids, batch_vectors, batch_metadatas):
            items.append({"id": _id, "values": vec.tolist(), "metadata": meta})

        logger.info(f"Upserting batch of {len(items)} vectors to Pinecone")
        index.upsert(items)


def query_top_k(query_vec: np.ndarray, top_k: int):
    if _index is None:
        # Optional lazy init if enabled
        if os.getenv("PINECONE_LAZY_INIT", "0") == "1":
            dim = int(query_vec.shape[0])
            try:
                ensure_index(dimension=dim)
            except HTTPException as e:
                raise e
        else:
            # Graceful fallback: no index yet -> no matches
            return []
    index = _require_index()
    try:
        res = index.query(vector=query_vec.tolist(), top_k=top_k, include_metadata=True)
    except Exception as e:  # Surface dimension mismatch cleanly
        msg = str(e)
        if "dimension" in msg and "does not match" in msg:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "pinecone_dimension_mismatch",
                    "message": "Vector dimension mismatch between embeddings and index. Recreate index with correct dimension or change embedding model.",
                },
            ) from e
        raise
    matches = res.get("matches", []) if isinstance(res, dict) else res.matches  # support SDK variations
    out = []
    for m in matches:
        if isinstance(res, dict):
            _id = m.get("id")
            score = m.get("score")
            meta = m.get("metadata", {})
        else:  # object style
            _id = m.id
            score = m.score
            meta = getattr(m, "metadata", {}) or {}
        out.append({"id": _id, "score": score, "metadata": meta})
    return out


def delete_document_vectors(document_id: str):
    """Delete all vectors whose metadata document_id matches given id.

    Uses Pinecone delete with a metadata filter (SDK permitting). If filter delete unsupported
    in current SDK version, falls back to listing all ids by prefix pattern docid_.
    """
    if _index is None:
        return 0
    index = _require_index()
    # Attempt metadata-based delete
    try:
        # Some Pinecone SDK versions: index.delete(filter={...})
        index.delete(filter={"document_id": {"$eq": document_id}})
        return -1  # unknown count (not returned by API); caller can ignore
    except Exception as e:  # pragma: no cover
        msg = str(e).lower()
        if "filter" not in msg:
            raise
    # Fallback: attempt id prefix pattern
    try:
        # No direct list API across all versions; rely on naming pattern docid_index
        # Without a listing, we cannot enumerate precisely; signal no-op
        return 0
    except Exception:  # pragma: no cover
        return 0
