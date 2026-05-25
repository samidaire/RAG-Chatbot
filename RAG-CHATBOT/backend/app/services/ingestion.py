import logging
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any
import io
import asyncio
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi import HTTPException
from pypdf import PdfReader
from PIL import Image
import pytesseract

from app.db.mongo import documents_col, chunks_col
from app.services.embedding import embed_texts
from app.services.pinecone_client import ensure_index, upsert_vectors
from app.services.s3 import upload_bytes
from app.core.settings import get_settings
from app.utils.chunking import chunk_text, stable_document_id

logger = logging.getLogger(__name__)

# Thread pool for CPU-intensive operations
_thread_pool = ThreadPoolExecutor(max_workers=4)


def _extract_text_from_page(page_data: Tuple[int, Any]) -> Tuple[int, str]:
    """Extract text from a single PDF page (thread-safe)."""
    page_num, page = page_data
    try:
        # First try to extract text from the PDF
        text_content = page.extract_text() or ""
        
        # If no text found, try OCR on the page images
        if not text_content.strip():
            logger.debug(f"No text found on page {page_num + 1}, attempting OCR...")
            try:
                # Extract embedded images from PDF
                ocr_texts = []
                if hasattr(page, 'images'):
                    for img_obj in page.images:
                        try:
                            img_data = img_obj.data
                            img = Image.open(io.BytesIO(img_data))
                            ocr_text = pytesseract.image_to_string(img, config='--psm 6')
                            if ocr_text.strip():
                                ocr_texts.append(ocr_text)
                        except Exception as e:
                            logger.debug(f"OCR failed for image on page {page_num + 1}: {e}")
                
                if ocr_texts:
                    text_content = "\n".join(ocr_texts)
                    logger.debug(f"OCR extracted {len(text_content)} characters from page {page_num + 1}")
                        
            except Exception as e:
                logger.debug(f"OCR processing failed for page {page_num + 1}: {e}")
        
        return page_num, text_content
        
    except Exception as e:
        logger.warning(f"Failed to process page {page_num + 1}: {e}")
        return page_num, ""


async def extract_pdf_text(data: bytes) -> str:
    """Optimized PDF text extraction with parallel processing."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(
            status_code=400, 
            detail={"error": "pdf_parse_failed", "message": str(e)}
        ) from e

    if not reader.pages:
        logger.warning("PDF has no pages")
        return ""

    # Process pages in parallel using thread pool
    page_data = [(i, page) for i, page in enumerate(reader.pages)]
    
    # Run page extraction in thread pool
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(_thread_pool, _extract_text_from_page, pd) 
        for pd in page_data
    ]
    
    # Gather results
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results, maintaining page order
    page_texts = {}
    has_text_content = False
    
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"Page extraction failed: {result}")
            continue
            
        page_num, text_content = result
        if text_content.strip():
            page_texts[page_num] = text_content
            has_text_content = True
    
    # Reconstruct text in page order
    final_texts = []
    for i in range(len(reader.pages)):
        if i in page_texts:
            final_texts.append(page_texts[i])
    
    final_text = "\n".join(final_texts)
    
    if not has_text_content:
        logger.warning("No text content extracted from PDF (neither text nor OCR)")
    else:
        logger.info(f"Successfully extracted {len(final_text)} characters from PDF")
    
    return final_text


def _decode_text_content(raw_bytes: bytes) -> str:
    """Optimized text decoding with fallback strategies."""
    # Try common encodings in order of preference
    encodings = ['utf-8', 'utf-16', 'latin1', 'cp1252']
    
    for encoding in encodings:
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    # Final fallback with error replacement
    return raw_bytes.decode('utf-8', errors='replace')


async def _batch_upsert_chunks(
    doc_hash: str, 
    chunks: List[str], 
    embeddings: List[List[float]]
) -> None:
    """Batch upsert chunks with optimized batch sizes."""
    if not chunks or not embeddings:
        return
    
    # Determine optimal batch size based on chunk length and embedding dimensions
    avg_chunk_len = sum(len(c) for c in chunks) / len(chunks)
    embedding_dim = len(embeddings[0])
    
    # Adjust batch size based on data size (rough heuristic)
    if avg_chunk_len > 2000 or embedding_dim > 1536:
        batch_size = 50
    else:
        batch_size = 100
    
    # Process in batches
    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i:i + batch_size]
        batch_embeddings = embeddings[i:i + batch_size]
        
        ids = [f"{doc_hash}_{j}" for j in range(i, i + len(batch_chunks))]
        metas = [{"document_id": doc_hash, "chunk_index": j} for j in range(i, i + len(batch_chunks))]
        
        try:
            upsert_vectors(ids, batch_embeddings, metas)
        except Exception as e:
            logger.error(f"Failed to upsert batch {i//batch_size + 1}: {e}")
            raise


async def _prepare_chunk_documents(
    doc_hash: str, 
    chunks: List[str], 
    now: datetime
) -> List[Dict[str, Any]]:
    """Prepare chunk documents for batch insertion."""
    return [
        {
            "_id": f"{doc_hash}_{i}",
            "document_id": doc_hash,
            "index": i,
            "text": chunk_text,
            "created_at": now,
            "text_length": len(chunk_text),  # Add for analytics
            "word_count": len(chunk_text.split()),  # Add for analytics
        }
        for i, chunk_text in enumerate(chunks)
    ]


async def process_document(
    raw_bytes: bytes,
    source_name: str,
    is_pdf: bool,
    chunk_size: int = 500,
    overlap: int = 50,
    token_chunk_size: Optional[int] = 380,
    token_overlap: Optional[int] = 60,
    store_original: bool = True,
    force_reprocess: bool = False,
) -> Tuple[str, int]:
    """
    Optimized document processing with parallel operations and better error handling.
    
    Returns (document_id, chunks_count).
    """
    settings = get_settings()
    doc_hash = stable_document_id(raw_bytes)
    docs = documents_col()
    
    # Check existing document
    existing = await docs.find_one({"_id": doc_hash})
    if existing and not force_reprocess:
        return doc_hash, existing.get("num_chunks", 0)
    
    # Clean up existing data if reprocessing
    if existing and force_reprocess:
        # Run cleanup in parallel
        cleanup_tasks = [
            chunks_col().delete_many({"document_id": doc_hash}),
            # Could also add Pinecone vector cleanup here if needed
        ]
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    
    # Enforce document cap (check after potential cleanup)
    if not existing:  # Only check cap for new documents
        total_docs = await docs.count_documents({})
        if total_docs >= 15:
            raise HTTPException(
                status_code=400, 
                detail={
                    "error": "document_cap_reached",
                    "message": "Maximum document limit (15) reached. Delete a document before adding more."
                }
            )
    
    # Extract text content
    try:
        if is_pdf:
            extracted_text = await extract_pdf_text(raw_bytes)
        else:
            # Run text decoding in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            extracted_text = await loop.run_in_executor(_thread_pool, _decode_text_content, raw_bytes)
    except Exception as e:
        logger.error(f"Text extraction failed for {source_name}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "text_extraction_failed", "message": str(e)}
        )
    
    # Validate extracted text
    if not extracted_text.strip():
        error_msg = (
            "The uploaded PDF document does not contain any readable text. "
            "Please ensure the document contains text content or try uploading a different file."
            if is_pdf else
            "The uploaded document does not contain any readable text. "
            "Please ensure the file contains text content."
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "no_text_in_document", "message": error_msg}
        )
    
    # Chunk text with enhanced parameters
    chunks = chunk_text(
        extracted_text, 
        chunk_size=chunk_size, 
        overlap=overlap,
        token_chunk_size=token_chunk_size,
        token_overlap=token_overlap
    )
    
    if not chunks:
        raise HTTPException(
            status_code=400, 
            detail={"error": "no_chunks", "message": "Chunking produced no chunks"}
        )
    
    logger.info(f"Generated {len(chunks)} chunks for document {source_name}")
    
    # Generate embeddings
    try:
        embeddings = await embed_texts(chunks)
        if not embeddings:
            raise HTTPException(
                status_code=500, 
                detail={"error": "embedding_empty", "message": "Embedding service returned no vectors"}
            )
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "embedding_failed", "message": str(e)}
        )
    
    # Ensure Pinecone index
    dim = len(embeddings[0])
    try:
        ensure_index(dimension=dim)
    except HTTPException:
        logger.exception("Failed ensuring Pinecone index")
        raise
    
    # Prepare concurrent operations
    now = datetime.utcnow()
    concurrent_tasks = []
    
    # 1. Upsert vectors to Pinecone
    concurrent_tasks.append(_batch_upsert_chunks(doc_hash, chunks, embeddings))
    
    # 2. Upload to S3 if configured
    s3_key: Optional[str] = None
    if settings.s3_bucket and store_original:
        s3_key = f"documents/{doc_hash}"
        concurrent_tasks.append(
            asyncio.get_event_loop().run_in_executor(_thread_pool, upload_bytes, raw_bytes, s3_key)
        )
    
    # 3. Prepare chunk documents for MongoDB
    chunk_docs_task = _prepare_chunk_documents(doc_hash, chunks, now)
    concurrent_tasks.append(chunk_docs_task)
    
    # Execute concurrent operations
    try:
        results = await asyncio.gather(*concurrent_tasks, return_exceptions=True)
        
        # Check for failures
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                operation_names = ["vector_upsert", "s3_upload", "chunk_prep"][i]
                logger.error(f"Failed {operation_names}: {result}")
                if i == 0:  # Vector upsert is critical
                    raise result
    except Exception as e:
        logger.error(f"Concurrent operations failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "processing_failed", "message": str(e)}
        )
    
    # Get prepared chunk documents
    chunk_docs = results[-1] if not isinstance(results[-1], Exception) else []
    
    # Update/insert document metadata
    doc_data = {
        "source_name": source_name,
        "s3_key": s3_key if s3_key is not None else (existing.get("s3_key") if existing else None),
        "num_chunks": len(chunks),
        "processing_status": "completed",
        "updated_at": now,
        "last_error": None,
        "file_size": len(raw_bytes),
        "text_length": len(extracted_text),
        "avg_chunk_size": sum(len(c) for c in chunks) / len(chunks),
    }
    
    if existing:
        await docs.update_one({"_id": doc_hash}, {"$set": doc_data})
    else:
        doc_data.update({
            "_id": doc_hash,
            "created_at": now,
        })
        await docs.insert_one(doc_data)
    
    # Insert chunk documents
    if chunk_docs:
        try:
            # Use ordered=False for better performance and partial success
            await chunks_col().insert_many(chunk_docs, ordered=False)
        except Exception as e:
            logger.error(f"Failed to insert chunk documents: {e}")
            # This is not critical enough to fail the entire process
    
    logger.info(f"Successfully processed document {source_name} ({len(chunks)} chunks)")
    return doc_hash, len(chunks)


# Cleanup function for the thread pool
def cleanup_thread_pool():
    """Clean up the thread pool on application shutdown."""
    global _thread_pool
    if _thread_pool:
        _thread_pool.shutdown(wait=True)