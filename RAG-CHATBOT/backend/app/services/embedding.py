import logging
import asyncio
from typing import List, Optional, Tuple
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from functools import wraps

import httpx
import numpy as np
from fastapi import HTTPException

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"

# Global client instance for connection pooling
_client: Optional[httpx.AsyncClient] = None


@dataclass
class EmbeddingMetrics:
    """Track embedding performance metrics."""
    total_requests: int = 0
    total_tokens: int = 0
    total_time: float = 0.0
    errors: int = 0
    rate_limits: int = 0


# Global metrics instance
_metrics = EmbeddingMetrics()


def track_metrics(func):
    """Decorator to track embedding metrics."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            _metrics.total_requests += 1
            _metrics.total_time += time.time() - start_time
            if args and isinstance(args[0], list):  # texts parameter
                _metrics.total_tokens += sum(len(text.split()) for text in args[0])
            return result
        except HTTPException as e:
            _metrics.errors += 1
            if e.status_code == 429:
                _metrics.rate_limits += 1
            raise
        except Exception:
            _metrics.errors += 1
            raise
    return wrapper


async def get_client() -> httpx.AsyncClient:
    """Get or create the global HTTP client with optimized settings."""
    global _client
    if _client is None or _client.is_closed:
        # Optimized client configuration
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=30.0
        )
        
        timeout = httpx.Timeout(
            connect=10.0,
            read=60.0,  # Increased for large embedding requests
            write=30.0,
            pool=10.0
        )
        
        _client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            http2=True,  # Enable HTTP/2 for better performance
            follow_redirects=True
        )
    
    return _client


async def close_client():
    """Close the global HTTP client."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def calculate_optimal_batch_size(texts: List[str], max_tokens: int = 8000) -> int:
    """Calculate optimal batch size based on text lengths and token limits."""
    if not texts:
        return 0
    
    # Rough token estimation (1 token ≈ 4 characters for English)
    avg_chars_per_text = sum(len(text) for text in texts) / len(texts)
    estimated_tokens_per_text = max(1, int(avg_chars_per_text / 4))
    
    # Conservative batch size calculation
    optimal_batch_size = min(
        len(texts),
        max(1, max_tokens // estimated_tokens_per_text),
        1000  # Hard limit from your original code
    )
    
    return optimal_batch_size


async def _embed_batch_with_retry(
    texts: List[str], 
    model: str, 
    api_key: str,
    max_retries: int = 3,
    base_delay: float = 1.0
) -> List[np.ndarray]:
    """Embed a batch of texts with exponential backoff retry logic."""
    client = await get_client()
    
    payload = {"model": model, "input": texts}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            resp = await client.post(OPENAI_EMBEDDINGS_URL, json=payload, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                embeddings = [np.asarray(item["embedding"], dtype=np.float32) for item in items]
                
                if len(embeddings) != len(texts):
                    raise ValueError(f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
                
                return embeddings
            
            # Handle specific error codes
            elif resp.status_code == 429:  # Rate limit
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + np.random.uniform(0, 1)
                    logger.warning(f"Rate limited, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise HTTPException(
                        status_code=429, 
                        detail={"error": "rate_limited", "message": "Rate limit hit. Retry later."}
                    )
            
            elif resp.status_code in (500, 502, 503, 504):  # Server errors
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Server error {resp.status_code}, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                    continue
            
            # For other errors, don't retry
            break
            
        except httpx.TimeoutException as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Timeout, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                last_exception = e
                continue
            else:
                raise HTTPException(
                    status_code=504, 
                    detail={"error": "timeout", "message": "Embedding request timed out"}
                )
        
        except httpx.HTTPError as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Network error, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            break
        
        except Exception as e:
            last_exception = e
            break
    
    # Handle final error states
    if hasattr(resp, 'status_code'):
        status = resp.status_code
        truncated_body = resp.text[:300]
        
        if status in (401, 403):
            logger.error("OpenAI auth/config error %s: %s", status, truncated_body)
            raise HTTPException(
                status_code=status, 
                detail={"error": "auth_error", "message": "Embedding authorization failed"}
            )
        
        if status == 429:
            logger.error("OpenAI rate limit exceeded after retries: %s", truncated_body)
            raise HTTPException(
                status_code=429, 
                detail={"error": "rate_limited", "message": "Rate limit exceeded after retries"}
            )
        
        if 500 <= status < 600:
            logger.error("OpenAI upstream failure %s: %s", status, truncated_body)
            raise HTTPException(
                status_code=502, 
                detail={"error": "upstream_error", "message": "Upstream embedding service error"}
            )
        
        logger.error("Unexpected OpenAI response %s: %s", status, truncated_body)
        raise HTTPException(
            status_code=502, 
            detail={"error": "unexpected_response", "message": f"Unexpected embedding status {status}"}
        )
    
    # Network or parsing error
    logger.exception("OpenAI embeddings error: %s", last_exception)
    raise HTTPException(
        status_code=502, 
        detail={"error": "network_error", "message": str(last_exception) if last_exception else "Unknown error"}
    )


@track_metrics
async def embed_texts(texts: List[str]) -> List[np.ndarray]:
    """
    Optimized embedding function with batching, retry logic, and connection pooling.
    
    Features:
    - Automatic batching based on content size
    - Exponential backoff retry for transient errors
    - Connection pooling for better performance
    - Metrics tracking
    - Parallel batch processing for large inputs
    """
    if not texts:
        return []
    
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503, 
            detail={"error": "missing_api_key", "message": "OPENAI_API_KEY not configured"}
        )
    
    # Enhanced length guard with better error message
    if len(texts) > 500:  # Increased limit for batch processing
        raise HTTPException(
            status_code=400, 
            detail={
                "error": "too_many_texts", 
                "message": f"Maximum 500 texts per embedding call, got {len(texts)}"
            }
        )
    
    # Calculate optimal batch size
    batch_size = calculate_optimal_batch_size(texts)
    
    # If batch size covers all texts, process in single batch
    if batch_size >= len(texts):
        try:
            return await _embed_batch_with_retry(texts, settings.embed_model, settings.openai_api_key)
        except Exception as e:
            logger.error(f"Single batch embedding failed: {e}")
            raise
    
    # Process in multiple batches
    logger.info(f"Processing {len(texts)} texts in batches of {batch_size}")
    
    batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
    
    # Process batches with limited concurrency to avoid rate limits
    max_concurrent_batches = min(3, len(batches))  # Limit concurrent batches
    semaphore = asyncio.Semaphore(max_concurrent_batches)
    
    async def process_batch(batch_texts: List[str]) -> List[np.ndarray]:
        async with semaphore:
            return await _embed_batch_with_retry(
                batch_texts, 
                settings.embed_model, 
                settings.openai_api_key
            )
    
    try:
        # Process batches concurrently
        batch_results = await asyncio.gather(
            *[process_batch(batch) for batch in batches],
            return_exceptions=False
        )
        
        # Flatten results
        all_embeddings = []
        for batch_embeddings in batch_results:
            all_embeddings.extend(batch_embeddings)
        
        if len(all_embeddings) != len(texts):
            logger.error("Total embedding count mismatch: expected %d got %d", len(texts), len(all_embeddings))
            raise HTTPException(
                status_code=502, 
                detail={"error": "count_mismatch", "message": "Total embeddings don't match input texts"}
            )
        
        logger.info(f"Successfully embedded {len(texts)} texts in {len(batches)} batches")
        return all_embeddings
        
    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        raise


async def embed_texts_streaming(texts: List[str]) -> List[np.ndarray]:
    """
    Alternative streaming approach for very large text collections.
    Processes texts in smaller batches with controlled rate limiting.
    """
    if not texts:
        return []
    
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503, 
            detail={"error": "missing_api_key", "message": "OPENAI_API_KEY not configured"}
        )
    
    # Smaller batches for streaming approach
    batch_size = min(50, calculate_optimal_batch_size(texts, max_tokens=4000))
    batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
    
    all_embeddings = []
    
    for i, batch in enumerate(batches):
        try:
            batch_embeddings = await _embed_batch_with_retry(
                batch, 
                settings.embed_model, 
                settings.openai_api_key
            )
            all_embeddings.extend(batch_embeddings)
            
            # Rate limiting: small delay between batches
            if i < len(batches) - 1:  # Don't delay after last batch
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Streaming batch {i + 1} failed: {e}")
            raise
    
    return all_embeddings


def get_embedding_metrics() -> dict:
    """Get current embedding metrics."""
    return {
        "total_requests": _metrics.total_requests,
        "total_tokens": _metrics.total_tokens,
        "total_time": _metrics.total_time,
        "errors": _metrics.errors,
        "rate_limits": _metrics.rate_limits,
        "avg_request_time": _metrics.total_time / max(1, _metrics.total_requests),
        "error_rate": _metrics.errors / max(1, _metrics.total_requests),
    }


def reset_metrics():
    """Reset embedding metrics."""
    global _metrics
    _metrics = EmbeddingMetrics()


@asynccontextmanager
async def embedding_client_context():
    """Context manager for proper client lifecycle management."""
    try:
        yield
    finally:
        await close_client()