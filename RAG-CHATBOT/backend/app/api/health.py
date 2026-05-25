from fastapi import APIRouter, Query
from app.core.settings import get_settings
from app.services.s3 import is_s3_enabled

def _pinecone_configured(settings) -> bool:
    return bool(settings.pinecone_api_key and settings.pinecone_index)

def _openai_configured(settings) -> bool:
    return bool(settings.openai_api_key)

def _mongo_configured(settings) -> bool:
    return bool(settings.mongo_uri and settings.mongo_db)

router = APIRouter(tags=["health"])

@router.get("/health")
async def health(verbose: int = Query(default=0, description="Set 1 to include configuration readiness flags")):
    if not verbose:
        return {"status": "ok"}
    settings = get_settings()
    return {
        "status": "ok",
        "config": {
            "openai": _openai_configured(settings),
            "pinecone": _pinecone_configured(settings),
            "mongo": _mongo_configured(settings),
            "s3": is_s3_enabled(),
            "embed_model": settings.embed_model,
            "llm_model": settings.llm_model,
            "pinecone_index": settings.pinecone_index,
            "s3_bucket": settings.s3_bucket,
        }
    }
