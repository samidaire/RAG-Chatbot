import logging
from fastapi import APIRouter, HTTPException, Query
from typing import List
from datetime import datetime
from app.db.mongo import conversations_col, messages_col
from app.services.conversation import delete_conversation, create_conversation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

@router.post("")
async def create_conversation_endpoint():
    conv_id = await create_conversation()
    return {"conversation_id": conv_id, "created": True}


@router.get("")
async def list_conversations(limit: int = Query(100, le=500)):
    cursor = conversations_col().find({}, {"_id": 1, "title": 1, "created_at": 1, "updated_at": 1, "meta.allowed_document_ids": 1}).sort("updated_at", -1).limit(limit)
    items: List[dict] = []
    async for c in cursor:
        conv_id = c.get("_id")
        # Count messages (could index/denormalize later)
        msg_count = await messages_col().count_documents({"conversation_id": conv_id})
        # Fetch last message preview (latest message by created_at)
        last_msg = await messages_col().find({"conversation_id": conv_id}).sort("created_at", -1).limit(1).to_list(length=1)
        preview = last_msg[0]["content"][:120] + ("..." if len(last_msg[0]["content"]) > 120 else "") if last_msg else ""
        items.append({
            "conversation_id": conv_id,
            "title": c.get("title"),
            "document_ids": c.get("meta", {}).get("allowed_document_ids", []),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "message_count": msg_count,
            "last_message_preview": preview,
        })
    return {"conversations": items}


@router.delete("/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str):
    success = await delete_conversation(conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail={"error": "conversation_not_found"})
    return {"conversation_id": conversation_id, "deleted": True}
