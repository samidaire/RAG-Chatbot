import uuid
from datetime import datetime
from typing import List, Literal, Optional

from app.db.mongo import conversations_col, messages_col

Role = Literal["user", "assistant", "system"]


async def create_conversation() -> str:
    _id = str(uuid.uuid4())
    now = datetime.utcnow()
    await conversations_col().insert_one({
        "_id": _id,
        "created_at": now,
        "updated_at": now,
        "title": None,
        "meta": {},
    })
    return _id


async def get_conversation(conversation_id: str) -> dict | None:
    return await conversations_col().find_one({"_id": conversation_id})


async def set_conversation_documents(conversation_id: str, document_ids: list[str]):
    now = datetime.utcnow()
    await conversations_col().update_one({"_id": conversation_id}, {"$set": {"meta.allowed_document_ids": document_ids, "updated_at": now}})


async def set_conversation_title(conversation_id: str, title: str):
    if not title or title.strip() == "":
        return  # Don't set empty titles
    now = datetime.utcnow()
    await conversations_col().update_one({"_id": conversation_id}, {"$set": {"title": title.strip(), "updated_at": now}})


async def append_message(conversation_id: str, role: Role, content: str, citations: Optional[list[str]] = None, meta: Optional[dict] = None) -> str:
    mid = str(uuid.uuid4())
    now = datetime.utcnow()
    await messages_col().insert_one({
        "_id": mid,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "citations": citations or [],
        "created_at": now,
        "meta": meta or {},
    })
    await conversations_col().update_one({"_id": conversation_id}, {"$set": {"updated_at": now}})
    return mid


async def get_recent_messages(conversation_id: str, limit: int = 6) -> List[dict]:
    cursor = messages_col().find({"conversation_id": conversation_id}).sort("created_at", -1).limit(limit)
    out = [doc async for doc in cursor]
    out.reverse()  # oldest first
    return out


async def list_messages(conversation_id: str, limit: int = 100) -> List[dict]:
    cursor = messages_col().find({"conversation_id": conversation_id}).sort("created_at", 1).limit(limit)
    return [doc async for doc in cursor]


async def clear_conversation(conversation_id: str) -> int:
    res = await messages_col().delete_many({"conversation_id": conversation_id})
    return res.deleted_count


async def delete_conversation(conversation_id: str) -> bool:
    # Delete messages first
    await messages_col().delete_many({"conversation_id": conversation_id})
    # Delete conversation document
    result = await conversations_col().delete_one({"_id": conversation_id})
    return result.deleted_count > 0
