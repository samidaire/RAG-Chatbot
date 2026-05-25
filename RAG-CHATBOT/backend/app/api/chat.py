import logging
from typing import Optional, List, Dict
from time import perf_counter

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.settings import get_settings
from app.services.embedding import embed_texts
from app.services.pinecone_client import query_top_k
from app.services.generation import generate_answer
from app.services.conversation import (
    create_conversation,
    append_message,
    get_recent_messages,
    list_messages,
    clear_conversation,
    get_conversation,
    set_conversation_documents,
    set_conversation_title,
)
from app.db.mongo import chunks_col, documents_col
import re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    top_k: int | None = None
    include_history: bool = False
    history_limit: int = 6
    min_score: float | None = None
    max_citations: int | None = None
    allowed_document_ids: list[str] | None = None
    debug_timings: bool = False
    strict: bool = False
    reset_conversation: bool = False


class ChatDebugRequest(BaseModel):
    query: str
    top_k: int | None = None
    score_threshold: float | None = None


@router.post("/debug")
async def chat_debug(req: ChatDebugRequest):
    """Enhanced debug endpoint with more detailed logging"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail={"error": "empty_query", "message": "Query is empty"})
    
    settings = get_settings()
    top_k = req.top_k or settings.top_k
    if top_k <= 0 or top_k > 50:
        raise HTTPException(status_code=400, detail={"error": "invalid_top_k", "message": "top_k must be between 1 and 50"})

    logger.info(f"Debug query: {req.query}")
    
    # Embed query
    embeds = await embed_texts([req.query])
    if not embeds:
        logger.error("Failed to embed query")
        raise HTTPException(status_code=500, detail={"error": "embed_failed", "message": "Failed to embed query"})
    
    logger.info(f"Embedding dimension: {len(embeds[0])}")
    
    # Ensure index attached
    try:
        from app.services.pinecone_client import ensure_index
        ensure_index(dimension=len(embeds[0]))
        logger.info("Pinecone index ensured")
    except Exception as e:
        logger.error(f"Failed to ensure index: {e}")
        pass
    
    # Query vector DB
    matches = query_top_k(embeds[0], top_k=top_k)
    logger.info(f"Raw matches found: {len(matches)}")

    chunk_ids = [m["id"] for m in matches]
    cursor = chunks_col().find({"_id": {"$in": chunk_ids}})
    chunk_map = {doc["_id"]: doc async for doc in cursor}
    logger.info(f"Chunks found in MongoDB: {len(chunk_map)}")

    enriched = []
    for rank, m in enumerate(matches):
        cid = m["id"]
        ch = chunk_map.get(cid)
        if not ch:
            enriched.append({
                "rank": rank,
                "chunk_id": cid,
                "missing": True,
                "score": m.get("score"),
            })
            continue
        preview = ch["text"][:200].replace("\n", " ") + ("..." if len(ch["text"]) > 200 else "")
        enriched.append({
            "rank": rank,
            "chunk_id": ch["_id"],
            "document_id": ch["document_id"],
            "chunk_index": ch["index"],
            "score": m.get("score"),
            "preview": preview,
        })

    # Determine reason if effectively empty
    reason = None
    if not matches:
        reason = "no_matches"
    else:
        scores = [m.get("score") for m in matches if m.get("score") is not None]
        if scores and req.score_threshold is not None:
            if all(s < req.score_threshold for s in scores):
                reason = "all_below_threshold"

    return {
        "query": req.query,
        "top_k": top_k,
        "score_threshold": req.score_threshold,
        "match_count": len(matches),
        "chunks_in_db": len(chunk_map),
        "reason": reason,
        "matches": enriched,
        "embedding_dimension": len(embeds[0]) if embeds else None,
    }


@router.get("/document-summary")
async def document_summary(allowed_document_ids: list[str] | None = Query(None, description="Document IDs to summarize")):
    """Get a summary of available documents and their content themes."""
    try:
        # Get document counts
        total_docs = await documents_col().count_documents({"processing_status": "completed"})
        total_chunks = await chunks_col().count_documents({})

        # Get documents
        query = {"processing_status": "completed"}
        if allowed_document_ids:
            query["_id"] = {"$in": allowed_document_ids}

        docs_cursor = documents_col().find(query, {"_id": 1, "source_name": 1, "num_chunks": 1})
        docs = [doc async for doc in docs_cursor]

        # Get sample chunks for content analysis
        chunk_query = {}
        if allowed_document_ids:
            chunk_query["document_id"] = {"$in": allowed_document_ids}

        sample_chunks_cursor = chunks_col().find(chunk_query, {"text": 1}).limit(20)
        sample_chunks = [chunk async for chunk in sample_chunks_cursor]

        # Extract common themes/topics
        if sample_chunks:
            all_text = " ".join([c["text"] for c in sample_chunks])
            words = all_text.lower().split()

            # Filter out common stop words and extract meaningful terms
            stop_words = {
                'that', 'this', 'with', 'from', 'they', 'have', 'been', 'were', 'which', 'their',
                'there', 'these', 'those', 'what', 'when', 'where', 'how', 'why', 'who', 'can',
                'will', 'would', 'could', 'should', 'about', 'after', 'before', 'during', 'through'
            }

            meaningful_words = {}
            for word in words:
                word = word.strip('.,!?()[]{}:;"\'')
                if len(word) > 3 and word not in stop_words and word.isalpha():
                    meaningful_words[word] = meaningful_words.get(word, 0) + 1

            top_topics = sorted(meaningful_words.items(), key=lambda x: x[1], reverse=True)[:15]
        else:
            top_topics = []

        return {
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "filtered_documents": len(docs),
            "documents": [
                {
                    "id": doc["_id"],
                    "name": doc.get("source_name", "Unknown"),
                    "chunks": doc.get("num_chunks", 0)
                }
                for doc in docs
            ],
            "content_themes": [word for word, count in top_topics],
            "sample_content": sample_chunks[0]["text"][:300] + "..." if sample_chunks else None
        }

    except Exception as e:
        logger.error(f"Failed to generate document summary: {e}")
        raise HTTPException(status_code=500, detail={"error": "summary_failed", "message": str(e)})


@router.post("")
async def chat(req: ChatRequest):
    """Fixed chat endpoint with better error handling and fallbacks"""
    t0 = perf_counter()
    
    if not req.query.strip():
        raise HTTPException(status_code=400, detail={"error": "empty_query", "message": "Query is empty"})

    settings = get_settings()
    top_k = req.top_k or settings.top_k
    if top_k <= 0 or top_k > 50:
        raise HTTPException(status_code=400, detail={"error": "invalid_top_k", "message": "top_k must be between 1 and 50"})

    logger.info(f"Chat query: {req.query}, conversation_id: {req.conversation_id}")

    conversation_id = req.conversation_id or await create_conversation()
    convo_meta_allowed: list[str] | None = None
    if req.conversation_id:
        convo = await get_conversation(conversation_id)
        if convo:
            convo_meta_allowed = (convo.get("meta", {}) or {}).get("allowed_document_ids")

    # Insert user message early
    await append_message(conversation_id, "user", req.query)

    # Build retrieval context
    t_embed_start = perf_counter()
    try:
        embeds = await embed_texts([req.query])
        if not embeds:
            raise ValueError("No embeddings returned")
        logger.info(f"Successfully embedded query, dimension: {len(embeds[0])}")
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        raise HTTPException(status_code=500, detail={"error": "embed_failed", "message": f"Failed to embed query: {str(e)}"})
    
    t_embed_end = perf_counter()

    # Ensure index attached
    try:
        from app.services.pinecone_client import ensure_index
        ensure_index(dimension=len(embeds[0]))
        logger.info("Pinecone index ensured successfully")
    except Exception as e:
        logger.warning(f"Failed to ensure index: {e}")

    # Query vector database
    t_retr_start = perf_counter()
    try:
        matches = query_top_k(embeds[0], top_k=top_k)
        logger.info(f"Vector search returned {len(matches)} matches")
        
        # Log first few matches for debugging
        for i, match in enumerate(matches[:3]):
            logger.info(f"Match {i}: id={match.get('id', 'N/A')}, score={match.get('score', 'N/A')}")
            
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        matches = []
        
    t_retr_end = perf_counter()

    # Apply relevance filtering if min_score provided
    filtered_matches = []
    if req.min_score is not None:
        for m in matches:
            sc = m.get("score")
            if sc is None or sc >= req.min_score:
                filtered_matches.append(m)
        logger.info(f"After min_score filter: {len(filtered_matches)} matches")
    else:
        filtered_matches = matches

    # Apply allowed document id filter if provided
    effective_allowed = req.allowed_document_ids if req.allowed_document_ids else convo_meta_allowed
    if effective_allowed:
        allow_set = set(effective_allowed)
        pre_filter_count = len(filtered_matches)
        filtered_matches = [m for m in filtered_matches if (m.get("metadata", {}) or {}).get("document_id") in allow_set]
        logger.info(f"After document filter: {len(filtered_matches)} matches (from {pre_filter_count})")

    # Fetch chunks from MongoDB
    chunk_ids = [m["id"] for m in filtered_matches]
    if chunk_ids:
        try:
            cursor = chunks_col().find({"_id": {"$in": chunk_ids}})
            chunk_map = {doc["_id"]: doc async for doc in cursor}
            logger.info(f"Found {len(chunk_map)} chunks in MongoDB for {len(chunk_ids)} IDs")
        except Exception as e:
            logger.error(f"Failed to fetch chunks from MongoDB: {e}")
            chunk_map = {}
    else:
        chunk_map = {}
        
    ordered_chunks = []
    for m in filtered_matches:
        cid = m["id"]
        doc = chunk_map.get(cid)
        if doc:
            ordered_chunks.append(doc)
        else:
            logger.warning(f"Chunk {cid} not found in MongoDB")

    # Chunk diversification: down-rank chunks already used in conversation
    try:
        recent_msgs = await get_recent_messages(conversation_id, limit=50)
        used_citation_ids: dict[str, int] = {}
        for msg in recent_msgs:
            for cit in msg.get("citations", []) or []:
                used_citation_ids[cit] = used_citation_ids.get(cit, 0) + 1
        if used_citation_ids:
            logger.info(f"Diversification: found {len(used_citation_ids)} previously used chunk ids")
            # Simple frequency penalty: sort by (usage_count, original_index)
            def usage_penalty(c):
                return used_citation_ids.get(c['_id'], 0)
            ordered_chunks.sort(key=lambda c: (usage_penalty(c), c.get('index', 0)))
    except Exception as e:
        logger.warning(f"Diversification step failed: {e}")

    logger.info(f"Final ordered_chunks count: {len(ordered_chunks)}")

    # Limit citations if requested
    if req.max_citations is not None and req.max_citations >= 0:
        original_count = len(ordered_chunks)
        ordered_chunks = ordered_chunks[:req.max_citations]
        chunk_ids = [c["_id"] for c in ordered_chunks]
        if original_count > len(ordered_chunks):
            logger.info(f"Limited citations from {original_count} to {len(ordered_chunks)}")

    # Prepare context text
    context_parts: List[str] = []
    for idx, c in enumerate(ordered_chunks):
        context_parts.append(f"[C{idx}] {c['text']}")
    context_block = "\n\n".join(context_parts) if context_parts else "No relevant context retrieved."
    
    logger.info(f"Context block length: {len(context_block)} characters")

    # Simplified lexical relevance logic (lenient)
    q_tokens = [t for t in re.findall(r"[a-zA-Z0-9]+", req.query.lower()) if len(t) > 2]
    BASIC_STOP = {
        'the','and','for','with','this','that','from','into','your','about','more','list','any','how','what','where','when','why','who',
        'can','could','should','would','will','may','might','must','have','has','had','get','got','make','made','use','used','using',
        'tell','show','find','search','look','see','know','need','want','help','please','does','did','done','its','it','are','is'
    }
    content_tokens = [t for t in q_tokens if t not in BASIC_STOP]

    logger.info(f"Query tokens: {q_tokens}")
    logger.info(f"Content tokens: {content_tokens}")

    # Removed strict short query and lexical overlap rejection: allow model to attempt answer or fall back later.

    # IMPROVED: Better fallback handling for generic queries
    if not ordered_chunks:
        logger.warning("No chunks available for context")
        if not matches:
            reason = "no_vector_matches"
            # If no documents are selected, try to provide some general information
            if not effective_allowed:
                try:
                    # Get total document and chunk counts
                    total_docs = await documents_col().count_documents({"processing_status": "completed"})
                    total_chunks = await chunks_col().count_documents({})

                    if total_docs > 0:
                        fallback_msg = f"I have {total_docs} document(s) with {total_chunks} chunks indexed, but couldn't find specific information matching your question: '{req.query}'. Try asking a more specific question or select specific documents to search within."
                        reason = "no_matches_but_data_exists"
                    else:
                        fallback_msg = "I don't have any relevant information in my knowledge base to answer your question. This might be because no documents have been uploaded and processed yet."
                except Exception as e:
                    logger.warning(f"Failed to get document counts: {e}")
                    fallback_msg = "I don't have any relevant information in my knowledge base to answer your question. This might be because the documents haven't been properly indexed or your query doesn't match the available content."
            else:
                fallback_msg = "I don't have any relevant information in my knowledge base to answer your question. This might be because the documents haven't been properly indexed or your query doesn't match the available content."
        elif req.min_score is not None and matches and not filtered_matches:
            reason = "all_below_min_score"
            fallback_msg = f"I found some potentially relevant information, but it didn't meet the minimum relevance threshold (score >= {req.min_score}). Try rephrasing your question or lowering the relevance threshold."
        else:
            reason = "chunks_not_found_in_db"
            # Check if this is a very generic query about document content
            query_lower = req.query.lower().strip()
            generic_queries = [
                "what does this document", "what's in this document", "what is this document",
                "tell me about this document", "summarize this document", "what does the document contain"
            ]
            is_generic = any(generic_query in query_lower for generic_query in generic_queries)

            if is_generic and effective_allowed:
                # For generic queries about selected documents, try to return some content anyway
                try:
                    # Get a few chunks from the selected documents to provide basic information
                    doc_ids = effective_allowed
                    sample_chunks_cursor = chunks_col().find(
                        {"document_id": {"$in": doc_ids}},
                        {"text": 1, "document_id": 1}
                    ).limit(5)  # Get more chunks for better overview
                    sample_chunks = [doc async for doc in sample_chunks_cursor]

                    if sample_chunks:
                        # Extract key topics/themes from the chunks
                        all_text = " ".join([c["text"] for c in sample_chunks])
                        words = all_text.lower().split()
                        # Simple keyword extraction (most common nouns)
                        common_words = {}
                        for word in words:
                            word = word.strip('.,!?()[]{}:;"\'')
                            if len(word) > 3 and word not in ['that', 'this', 'with', 'from', 'they', 'have', 'been', 'were', 'which', 'their', 'there', 'these', 'those']:
                                common_words[word] = common_words.get(word, 0) + 1

                        top_topics = sorted(common_words.items(), key=lambda x: x[1], reverse=True)[:10]
                        topics_str = ", ".join([word for word, count in top_topics[:5]])

                        context_preview = " ".join([c["text"][:150] + "..." for c in sample_chunks[:2]])
                        fallback_msg = f"This document appears to contain information about: {topics_str}. Here's a sample: {context_preview[:400]}... Try asking more specific questions about these topics."
                        reason = "generic_query_fallback"
                    else:
                        selected_docs = effective_allowed
                        doc_list = ", ".join(selected_docs) if selected_docs else "the selected document"
                        fallback_msg = (
                            f"Sorry, the selected document(s) do not have relevant information regarding your question: '{req.query}'. "
                            "Please try a different document or rephrase your question."
                        )
                except Exception as e:
                    logger.warning(f"Failed to get sample chunks for generic query: {e}")
                    selected_docs = effective_allowed if effective_allowed else []
                    doc_list = ", ".join(selected_docs) if selected_docs else "the selected document"
                    fallback_msg = (
                        f"Sorry, the selected document(s) do not have relevant information regarding your question: '{req.query}'. "
                        "Please try a different document or rephrase your question."
                    )
            else:
                # Improved graceful message
                selected_docs = effective_allowed if effective_allowed else []
                doc_list = ", ".join(selected_docs) if selected_docs else "the selected document"
                fallback_msg = (
                    f"Sorry, the selected document(s) do not have relevant information regarding your question: '{req.query}'. "
                    "Please try a different document or rephrase your question."
                )
        
        await append_message(conversation_id, "assistant", fallback_msg, citations=[])
        timings = _build_timings(req, t0, t_embed_start, t_embed_end, t_retr_start, t_retr_end, perf_counter())
        return {
            "conversation_id": conversation_id,
            "answer": fallback_msg,
            "citations": [],
            "match_count": len(matches),
            "filtered_count": len(filtered_matches),
            "used_citations": 0,
            "active_document_ids": effective_allowed,
            "applied_min_score": req.min_score,
            "reason": reason,
            **({"timings": timings} if timings else {}),
        }

    # Get conversation history (exclude current user message and allow reset)
    history_msgs = []
    if req.reset_conversation:
        logger.info("Conversation reset forced - ignoring all history for this request")
    elif req.include_history:
        try:
            past = await get_recent_messages(conversation_id, limit=req.history_limit)
            if past:
                # Exclude the very latest (the user query just appended above)
                trimmed = past[:-1] if len(past) > 0 else []
                for m in trimmed:
                    if m["role"] in ("user", "assistant"):
                        history_msgs.append({"role": m["role"], "content": m["content"]})
            logger.info(f"Including conversation history: {len(history_msgs)} messages (limit={req.history_limit})")
        except Exception as e:
            logger.error(f"Failed to load conversation history: {e}")
    else:
        logger.info("Conversation history EXCLUDED - treating as isolated query")

    # IMPROVED: Better system prompt
    system_prompt = (
        "You are a helpful AI assistant. Treat each request independently unless prior turns are explicitly provided. "
        "Answer ONLY using the supplied context chunks. Cite sources with [C#]. "
        "If the context lacks the information to answer, respond that you cannot find the answer in the provided documents and do not invent details."
    )

    # Build fresh messages array
    messages = [{"role": "system", "content": system_prompt}]
    if req.include_history and history_msgs:
        messages.extend(history_msgs)
    messages.append({"role": "user", "content": f"Question: {req.query}\n\nContext:\n{context_block}"})

    # Debug log of messages (truncate long contents)
    try:
        log_preview = []
        for m in messages:
            content = m.get("content", "")
            if len(content) > 300:
                content = content[:300] + "...<truncated>"
            log_preview.append({"role": m.get("role"), "content": content})
        logger.info(f"LLM messages payload: {log_preview}")
    except Exception as e:
        logger.warning(f"Failed to log LLM messages preview: {e}")

    # Generate answer
    t_gen_start = perf_counter()
    try:
        answer = await generate_answer(messages)
        logger.info(f"Generated answer length: {len(answer)} characters")
    except Exception as e:
        logger.error(f"Answer generation failed: {e}")
        answer = f"I apologize, but I encountered an error while generating an answer to your question. Please try again or rephrase your query. Error: {str(e)}"
    
    t_gen_end = perf_counter()

    # Answer dedupe: compare to last assistant message (if any)
    dedupe_reason = None
    try:
        last_two = await get_recent_messages(conversation_id, limit=4)
        prev_assistant = None
        for m in reversed(last_two):
            if m["role"] == "assistant":
                prev_assistant = m["content"]
                break
        normalized_new = " ".join(answer.strip().lower().split())
        if prev_assistant:
            normalized_prev = " ".join(prev_assistant.strip().lower().split())
            if normalized_prev == normalized_new:
                logger.info("Answer dedupe triggered - replacing with no_new_information")
                answer = "I have already provided this information; no new details are available in the documents for your latest query."
                dedupe_reason = "duplicate_answer"
    except Exception as e:
        logger.warning(f"Answer dedupe step failed: {e}")

    # Store assistant message with citations
    await append_message(conversation_id, "assistant", answer, citations=chunk_ids)

    # Generate conversation title from first assistant response
    try:
        # Check if this conversation has only one assistant message (the current one)
        all_messages = await list_messages(conversation_id, limit=10)
        user_messages = [m for m in all_messages if m["role"] == "user"]
        assistant_messages = [m for m in all_messages if m["role"] == "assistant"]
        
        logger.info(f"Title generation check - conversation {conversation_id}: {len(user_messages)} user, {len(assistant_messages)} assistant messages")
        
        # Only generate title if this is the first interaction (1 user + 1 assistant message)
        if len(user_messages) == 1 and len(assistant_messages) == 1:
            logger.info(f"Generating title for first assistant response in conversation {conversation_id}")
            # Generate title from the first 50-100 characters of the answer
            title_text = answer.strip()
            logger.info(f"Original answer: {title_text[:100]}...")
            
            # Remove markdown formatting and clean up
            title_text = re.sub(r'\[C\d+\]', '', title_text)  # Remove citations
            title_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', title_text)  # Remove bold
            title_text = re.sub(r'\*([^*]+)\*', r'\1', title_text)  # Remove italic
            title_text = re.sub(r'`([^`]+)`', r'\1', title_text)  # Remove inline code
            title_text = re.sub(r'#+\s*', '', title_text)  # Remove headers
            title_text = ' '.join(title_text.split())  # Normalize whitespace
            
            logger.info(f"Cleaned title text: '{title_text}' (length: {len(title_text)})")
            
            # Take first 60 characters, cut at word boundary if possible
            if len(title_text) > 60:
                title_text = title_text[:60]
                last_space = title_text.rfind(' ')
                if last_space > 30:  # Only cut at space if it's not too short
                    title_text = title_text[:last_space]
            
            final_title = title_text.strip()
            logger.info(f"Final title: '{final_title}' (length: {len(final_title)})")
            
            if final_title and len(final_title) >= 3:  # Only set if we have meaningful content
                await set_conversation_title(conversation_id, final_title)
                logger.info(f"Successfully set conversation title: '{final_title}' for conversation {conversation_id}")
            else:
                logger.warning(f"Title too short, not setting: '{final_title}' (length: {len(final_title)})")
        else:
            logger.info(f"Not generating title - {len(user_messages)} user, {len(assistant_messages)} assistant messages")
    except Exception as e:
        logger.error(f"Failed to generate conversation title: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Don't fail the request if title generation fails

    # Fetch document metadata for source names
    doc_ids = {c["document_id"] for c in ordered_chunks}
    doc_map = {}
    if doc_ids:
        try:
            docs_cursor = documents_col().find({"_id": {"$in": list(doc_ids)}})
            doc_map = {d["_id"]: d async for d in docs_cursor}
        except Exception as e:
            logger.error(f"Failed to fetch document metadata: {e}")

    # Build citation details
    citation_details = []
    for idx, c in enumerate(ordered_chunks):
        snippet = c["text"][:200].replace("\n", " ") + ("..." if len(c["text"]) > 200 else "")
        doc_meta = doc_map.get(c["document_id"], {})
        item = {
            "chunk_id": c["_id"],
            "chunk_index": c["index"],
            "label": f"C{idx}",
            "document_id": c["document_id"],
            "source_name": doc_meta.get("source_name"),
            "snippet": snippet,
        }
        citation_details.append(item)

    timings = _build_timings(req, t0, t_embed_start, t_embed_end, t_retr_start, t_retr_end, t_gen_end)
    
    if timings:
        logger.info(
            "Chat completed - conversation_id=%s embed=%.3fs retrieve=%.3fs gen=%.3fs total=%.3fs matches=%d used=%d", 
            conversation_id, timings["embed_sec"], timings["retrieve_sec"], 
            timings["generation_sec"], timings["total_sec"], len(matches), len(citation_details)
        )

    response_payload = {
        "conversation_id": conversation_id,
        "answer": answer,
        "citations": citation_details,
        "match_count": len(matches),
        "filtered_count": len(filtered_matches),
        "used_citations": len(citation_details),
        "active_document_ids": effective_allowed,
        "applied_min_score": req.min_score,
        **({"timings": timings} if timings else {}),
    }
    if dedupe_reason:
        response_payload["reason"] = dedupe_reason
    return response_payload


def _build_timings(req: ChatRequest, t0: float, t_embed_start: float, t_embed_end: float, 
                  t_retr_start: float, t_retr_end: float, t_gen_end: float) -> Dict[str, float] | None:
    """Helper to build timing dictionary"""
    if not req.debug_timings:
        return None
    
    t_total = perf_counter() - t0
    return {
        "embed_sec": t_embed_end - t_embed_start,
        "retrieve_sec": t_retr_end - t_retr_start,
        "generation_sec": t_gen_end - t_retr_end if t_gen_end > t_retr_end else 0.0,
        "total_sec": t_total,
    }


class ConversationDocumentsRequest(BaseModel):
    document_ids: list[str]


@router.patch("/documents/{conversation_id}")
async def set_conversation_docs(conversation_id: str, req: ConversationDocumentsRequest):
    if len(req.document_ids) > 15:
        raise HTTPException(status_code=400, detail={"error": "too_many_documents", "message": "Maximum 15 documents per conversation"})
    await set_conversation_documents(conversation_id, req.document_ids)
    return {"conversation_id": conversation_id, "document_ids": req.document_ids, "status": "updated"}


@router.get("/history")
async def chat_history(conversation_id: str, limit: int = 100):
    msgs = await list_messages(conversation_id, limit=limit)
    return {"conversation_id": conversation_id, "messages": [
        {"role": m["role"], "content": m["content"], "created_at": m["created_at"], "citations": m.get("citations", [])}
        for m in msgs
    ]}


@router.delete("/clear/{conversation_id}")
async def chat_clear(conversation_id: str):
    deleted = await clear_conversation(conversation_id)
    return {"conversation_id": conversation_id, "deleted_messages": deleted}