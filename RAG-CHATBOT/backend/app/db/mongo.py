from typing import Optional
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.settings import get_settings

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

logger = logging.getLogger(__name__)

async def init_mongo() -> None:
    global _client, _db
    settings = get_settings()
    if _client is None:
        logger.info("Initializing MongoDB client uri=%s db=%s", settings.mongo_uri, settings.mongo_db)
    _client = AsyncIOMotorClient(settings.mongo_uri)
    _db = _client[settings.mongo_db]

async def close_mongo() -> None:
    global _client, _db
    if _client:
        logger.info("Closing MongoDB client")
        _client.close()
    _client = None
    _db = None

def get_db() -> AsyncIOMotorDatabase:
    assert _db is not None, "MongoDB not initialized"
    return _db

# Convenience collection accessors (optional for clarity)

def documents_col():
    return get_db()["documents"]

def chunks_col():
    return get_db()["chunks"]

def conversations_col():
    return get_db()["conversations"]

def messages_col():
    return get_db()["messages"]

def upload_jobs_col():
    return get_db()["upload_jobs"]


async def ensure_indexes() -> None:
    """Create necessary MVP indexes (idempotent)."""
    try:
        await chunks_col().create_index("document_id")
        await chunks_col().create_index([("document_id", 1), ("index", 1)])
        await messages_col().create_index([("conversation_id", 1), ("created_at", -1)])
        await conversations_col().create_index([("updated_at", -1)])
        await documents_col().create_index([("created_at", -1)])
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed creating indexes: %s", e)
