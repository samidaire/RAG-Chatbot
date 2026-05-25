import logging
from typing import List, Dict, Optional, AsyncGenerator, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import time
from functools import wraps
import json

import httpx
from fastapi import HTTPException

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Global client instance for connection pooling
_chat_client: Optional[httpx.AsyncClient] = None


@dataclass
class ChatMetrics:
    """Track chat completion performance metrics."""
    total_requests: int = 0
    total_tokens_sent: int = 0
    total_tokens_received: int = 0
    total_time: float = 0.0
    errors: int = 0
    rate_limits: int = 0
    timeouts: int = 0
    model_usage: Dict[str, int] = field(default_factory=dict)


# Global metrics instance
_chat_metrics = ChatMetrics()


def track_chat_metrics(func):
    """Decorator to track chat completion metrics."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            _chat_metrics.total_requests += 1
            _chat_metrics.total_time += time.time() - start_time
            
            # Track model usage if available in kwargs
            if 'model' in kwargs:
                model = kwargs['model']
                _chat_metrics.model_usage[model] = _chat_metrics.model_usage.get(model, 0) + 1
            
            # Estimate tokens (rough approximation)
            if args and isinstance(args[0], list):  # messages parameter
                estimated_tokens = sum(
                    len(str(msg.get('content', '')).split()) 
                    for msg in args[0] 
                    if isinstance(msg, dict)
                )
                _chat_metrics.total_tokens_sent += estimated_tokens
                _chat_metrics.total_tokens_received += len(str(result).split())
            
            return result
        except HTTPException as e:
            _chat_metrics.errors += 1
            if e.status_code == 429:
                _chat_metrics.rate_limits += 1
            elif e.status_code == 504:
                _chat_metrics.timeouts += 1
            raise
        except Exception:
            _chat_metrics.errors += 1
            raise
    return wrapper


async def get_chat_client() -> httpx.AsyncClient:
    """Get or create the global chat HTTP client with optimized settings."""
    global _chat_client
    if _chat_client is None or _chat_client.is_closed:
        # Optimized client configuration for chat completions
        limits = httpx.Limits(
            max_keepalive_connections=10,
            max_connections=50,
            keepalive_expiry=60.0  # Longer keepalive for chat sessions
        )
        
        timeout = httpx.Timeout(
            connect=15.0,
            read=120.0,  # Longer read timeout for complex completions
            write=30.0,
            pool=15.0
        )
        
        _chat_client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            http2=True,
            follow_redirects=True
        )
    
    return _chat_client


async def close_chat_client():
    """Close the global chat HTTP client."""
    global _chat_client
    if _chat_client and not _chat_client.is_closed:
        await _chat_client.aclose()
        _chat_client = None


def validate_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Validate and clean message format."""
    if not messages:
        raise HTTPException(
            status_code=400,
            detail={"error": "empty_messages", "message": "Messages list cannot be empty"}
        )
    
    valid_roles = {"system", "user", "assistant", "function", "tool"}
    cleaned_messages = []
    
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_message_format", "message": f"Message {i} must be a dictionary"}
            )
        
        role = msg.get("role")
        content = msg.get("content")
        
        if not role or role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_role", "message": f"Message {i} has invalid role: {role}"}
            )
        
        if not content or not isinstance(content, str):
            # Allow empty content for some special cases (like function calls)
            if role not in {"function", "tool"}:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_content", "message": f"Message {i} has invalid content"}
                )
        
        # Clean and validate the message
        cleaned_msg = {
            "role": role,
            "content": content or ""
        }
        
        # Preserve additional fields if present (like function_call, tool_calls, etc.)
        for key, value in msg.items():
            if key not in cleaned_msg and value is not None:
                cleaned_msg[key] = value
        
        cleaned_messages.append(cleaned_msg)
    
    return cleaned_messages


def estimate_token_count(messages: List[Dict[str, str]]) -> int:
    """Rough estimation of token count for messages."""
    total_chars = sum(
        len(str(msg.get('content', ''))) + len(str(msg.get('role', ''))) 
        for msg in messages
    )
    # Rough approximation: 1 token ≈ 4 characters for English
    return max(1, total_chars // 4)


async def _make_chat_request_with_retry(
    payload: Dict[str, Any],
    headers: Dict[str, str],
    max_retries: int = 2,
    base_delay: float = 1.0
) -> httpx.Response:
    """Make chat request with retry logic for transient failures."""
    client = await get_chat_client()
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            resp = await client.post(OPENAI_CHAT_URL, json=payload, headers=headers)
            
            # Don't retry on successful responses or client errors (4xx except 429)
            if resp.status_code < 500 and resp.status_code != 429:
                return resp
            
            # Retry on server errors (5xx) and rate limits (429)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Chat API error {resp.status_code}, retrying in {delay:.2f}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue
            
            return resp
            
        except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Chat timeout, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                last_exception = e
                continue
            else:
                raise HTTPException(
                    status_code=504,
                    detail={"error": "timeout", "message": "LLM request timed out after retries"}
                )
        
        except httpx.HTTPError as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Chat network error, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue
            break
    
    # If we get here, all retries failed
    if last_exception:
        logger.exception("Chat completion network error after retries")
        raise HTTPException(
            status_code=502,
            detail={"error": "network_error", "message": str(last_exception)}
        )
    
    return resp  # Return the last response for error handling


def extract_provider_error_message(body_json: Optional[Dict], raw_text: str) -> str:
    """Extract meaningful error message from provider response."""
    if not isinstance(body_json, dict):
        return raw_text[:200] if raw_text else "Unknown error"
    
    # Try various common error message fields
    error_paths = [
        ["error", "message"],
        ["error", "code"],
        ["message"],
        ["detail"],
        ["error"]
    ]
    
    for path in error_paths:
        value = body_json
        for key in path:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                break
        else:
            if isinstance(value, str) and value:
                return value
            elif isinstance(value, dict) and "message" in value:
                return str(value["message"])
    
    # Fallback to truncated raw response
    return raw_text[:200] if raw_text else "Provider error without details"


@track_chat_metrics
async def generate_answer(
    messages: List[Dict[str, str]], 
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    **kwargs
) -> str:
    """
    Enhanced chat completion with validation, retry logic, and better error handling.
    
    Args:
        messages: List of chat messages
        temperature: Sampling temperature (0.0-2.0)
        max_tokens: Maximum tokens to generate
        model: Model to use (overrides settings)
        **kwargs: Additional OpenAI API parameters
    
    Returns:
        Generated response content
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail={"error": "missing_api_key", "message": "OPENAI_API_KEY not configured"}
        )
    
    # Validate and clean messages
    cleaned_messages = validate_messages(messages)
    
    # Estimate token usage for logging
    estimated_tokens = estimate_token_count(cleaned_messages)
    if estimated_tokens > 100000:  # Large request warning
        logger.warning(f"Large chat request: ~{estimated_tokens} tokens")
    
    # Prepare payload with validation
    temp = 0.2 if temperature is None else max(0.0, min(float(temperature), 2.0))
    
    payload = {
        "model": model or settings.llm_model,
        "messages": cleaned_messages,
        "temperature": temp,
    }
    
    # Add optional parameters
    if max_tokens is not None:
        payload["max_tokens"] = max(1, min(int(max_tokens), 128000))  # Reasonable limits
    
    # Add any additional parameters
    for key, value in kwargs.items():
        if key not in payload and value is not None:
            payload[key] = value
    
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    
    # Make request with retry logic
    try:
        resp = await _make_chat_request_with_retry(payload, headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Chat completion unexpected error")
        raise HTTPException(
            status_code=502,
            detail={"error": "unexpected_error", "message": str(e)}
        )
    
    status = resp.status_code
    
    if status == 200:
        try:
            data = resp.json()
            
            # Enhanced response parsing with validation
            if "choices" not in data or not data["choices"]:
                raise ValueError("No choices in response")
            
            choice = data["choices"][0]
            if "message" not in choice:
                raise ValueError("No message in choice")
            
            message = choice["message"]
            content = message.get("content", "")
            
            # Log usage statistics if available
            if "usage" in data:
                usage = data["usage"]
                logger.debug(f"Token usage: {usage}")
            
            return content
            
        except Exception as e:
            logger.exception("Malformed chat response: %s", resp.text[:500])
            raise HTTPException(
                status_code=502,
                detail={"error": "parse_error", "message": f"Malformed LLM response: {str(e)}"}
            ) from e
    
    # Enhanced error handling
    raw_text = resp.text
    truncated = raw_text[:600]
    
    try:
        body_json = resp.json()
    except Exception:
        body_json = None
    
    if status in (401, 403):
        error_msg = extract_provider_error_message(body_json, raw_text)
        raise HTTPException(
            status_code=status,
            detail={
                "error": "auth_error",
                "message": "LLM authorization failed",
                "provider_message": error_msg
            }
        )
    
    if status == 400:
        error_msg = extract_provider_error_message(body_json, raw_text)
        raise HTTPException(
            status_code=400,  # Return 400 for client errors
            detail={
                "error": "llm_bad_request",
                "message": error_msg,
                "status": 400,
            }
        )
    
    if status == 429:
        error_msg = extract_provider_error_message(body_json, raw_text)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": "Rate limit exceeded",
                "provider_message": error_msg
            }
        )
    
    if 500 <= status < 600:
        error_msg = extract_provider_error_message(body_json, raw_text)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "upstream_error",
                "message": "Upstream LLM error",
                "provider_message": error_msg,
                "status": status
            }
        )
    
    # Unexpected status codes
    error_msg = extract_provider_error_message(body_json, raw_text)
    raise HTTPException(
        status_code=502,
        detail={
            "error": "unexpected_status",
            "message": f"Unexpected LLM status {status}",
            "provider_message": error_msg,
            "status": status
        }
    )


@track_chat_metrics
async def generate_answer_stream(
    messages: List[Dict[str, str]], 
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    **kwargs
) -> AsyncGenerator[str, None]:
    """
    Streaming chat completion for real-time responses.
    
    Yields response content chunks as they arrive.
    """
    settings = get_settings()
    
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail={"error": "missing_api_key", "message": "OPENAI_API_KEY not configured"}
        )
    
    # Validate and clean messages
    cleaned_messages = validate_messages(messages)
    
    # Prepare payload for streaming
    temp = 0.2 if temperature is None else max(0.0, min(float(temperature), 2.0))
    
    payload = {
        "model": model or settings.llm_model,
        "messages": cleaned_messages,
        "temperature": temp,
        "stream": True,  # Enable streaming
    }
    
    if max_tokens is not None:
        payload["max_tokens"] = max(1, min(int(max_tokens), 128000))
    
    # Add additional parameters
    for key, value in kwargs.items():
        if key not in payload and value is not None:
            payload[key] = value
    
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    
    client = await get_chat_client()
    
    try:
        async with client.stream("POST", OPENAI_CHAT_URL, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "streaming_error",
                        "message": f"Streaming failed with status {response.status_code}",
                        "body": error_text.decode()[:300]
                    }
                )
            
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data_str = chunk[6:]  # Remove "data: " prefix
                    if data_str.strip() == "[DONE]":
                        break
                    
                    try:
                        data = json.loads(data_str)
                        if "choices" in data and data["choices"]:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                    except json.JSONDecodeError:
                        continue  # Skip malformed chunks
                        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Streaming completion error")
        raise HTTPException(
            status_code=502,
            detail={"error": "streaming_error", "message": str(e)}
        )


def get_chat_metrics() -> Dict[str, Any]:
    """Get current chat completion metrics."""
    return {
        "total_requests": _chat_metrics.total_requests,
        "total_tokens_sent": _chat_metrics.total_tokens_sent,
        "total_tokens_received": _chat_metrics.total_tokens_received,
        "total_time": _chat_metrics.total_time,
        "errors": _chat_metrics.errors,
        "rate_limits": _chat_metrics.rate_limits,
        "timeouts": _chat_metrics.timeouts,
        "model_usage": dict(_chat_metrics.model_usage),
        "avg_request_time": _chat_metrics.total_time / max(1, _chat_metrics.total_requests),
        "error_rate": _chat_metrics.errors / max(1, _chat_metrics.total_requests),
        "rate_limit_rate": _chat_metrics.rate_limits / max(1, _chat_metrics.total_requests),
    }


def reset_chat_metrics():
    """Reset chat completion metrics."""
    global _chat_metrics
    _chat_metrics = ChatMetrics()


@asynccontextmanager
async def chat_client_context():
    """Context manager for proper chat client lifecycle management."""
    try:
        yield
    finally:
        await close_chat_client()