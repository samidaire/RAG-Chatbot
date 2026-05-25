import hashlib
import re
from typing import List, Optional, Tuple

try:  # optional dependency
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover
    tiktoken = None  # type: ignore


def stable_document_id(raw_bytes: bytes) -> str:
    """Return a stable hex id for a document (sha256)."""
    return hashlib.sha256(raw_bytes).hexdigest()


def _char_chunk_fallback(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Optimized character-based chunking with word boundaries."""
    if not text:
        return []
    
    res: List[str] = []
    start = 0
    text_len = len(text)
    min_chunk_size = int(chunk_size * 0.6)  # Pre-calculate
    
    while start < text_len:
        end = min(start + chunk_size, text_len)
        
        # Find word boundary if not at end
        if end < text_len and not text[end].isspace():
            # Look backward for word boundary, but don't go too far
            boundary_end = end
            while boundary_end > start + min_chunk_size and not text[boundary_end - 1].isspace():
                boundary_end -= 1
            if boundary_end > start + min_chunk_size:
                end = boundary_end
        
        piece = text[start:end].strip()
        if piece:
            res.append(piece)
        
        if end >= text_len:
            break
            
        # Calculate next start with overlap
        start = max(end - overlap, start + min_chunk_size) if overlap > 0 else end
    
    return res


def _split_sentences_optimized(text: str) -> List[str]:
    """Optimized sentence splitting with better regex."""
    # More comprehensive sentence boundary detection
    sentence_pattern = re.compile(
        r'(?<=[.!?])'  # After sentence ending punctuation
        r'(?:\s*["\'\)]*\s*)'  # Optional quotes/parens and whitespace
        r'(?=[A-Z]|\s*$)',  # Before capital letter or end
        re.MULTILINE
    )
    
    sentences = sentence_pattern.split(text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _create_optimized_overlap(text: str, enc, token_overlap: int) -> str:
    """Create optimal overlap text that fits within token limit."""
    if token_overlap <= 0:
        return ""
    
    # Encode the entire text and find the optimal substring
    tokens = enc.encode(text)
    if len(tokens) <= token_overlap:
        return text
    
    # Find the best substring within token_overlap
    # Try to end at a word boundary
    overlap_tokens = tokens[-token_overlap:]
    overlap_text = enc.decode(overlap_tokens)
    
    # If we cut off in the middle of a word, try to end at the last space
    if not overlap_text[-1].isspace() and ' ' in overlap_text:
        last_space = overlap_text.rfind(' ')
        if last_space > len(overlap_text) * 0.7:  # Don't reduce too much
            overlap_text = overlap_text[:last_space + 1]
    
    return overlap_text.strip()


def _chunk_with_encoder(
    text: str, 
    enc, 
    chunk_size: int, 
    overlap: int, 
    token_chunk_size: int, 
    token_overlap: int
) -> List[str]:
    """Core chunking logic using a pre-loaded encoder."""
    if not text:
        return []
    
    sentences = _split_sentences_optimized(text)
    if not sentences:
        return _char_chunk_fallback(text, chunk_size, overlap)
    
    chunks: List[str] = []
    current_chunk: List[str] = []
    current_tokens = 0
    
    for sent in sentences:
        sent_tokens = len(enc.encode(sent))
        
        # Handle oversized sentences
        if sent_tokens > token_chunk_size:
            # Flush current chunk if it has content
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_tokens = 0
            
            # Split oversized sentence using character fallback
            oversized_chunks = _char_chunk_fallback(sent, chunk_size, overlap)
            chunks.extend(oversized_chunks)
            continue
        
        # Check if adding this sentence would exceed limit
        if current_tokens + sent_tokens > token_chunk_size:
            # Flush current chunk
            chunks.append(" ".join(current_chunk))
            
            # Create overlap from current chunk
            overlap_text = _create_optimized_overlap(" ".join(current_chunk), enc, token_overlap)
            
            # Start new chunk with overlap
            current_chunk = [overlap_text] if overlap_text else []
            current_tokens = len(enc.encode(overlap_text)) if overlap_text else 0
        
        current_chunk.append(sent)
        current_tokens += sent_tokens
    
    # Add remaining content
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    
    return chunks if chunks else _char_chunk_fallback(text, chunk_size, overlap)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    token_chunk_size: Optional[int] = 380,
    token_overlap: Optional[int] = 60,
    encoding_name: str = "cl100k_base",
) -> List[str]:
    """Optimized token-aware chunking with sentence-friendly packing."""
    if not text:
        return []

    # Early fallback check
    if tiktoken is None or token_chunk_size is None:
        return _char_chunk_fallback(text, chunk_size, overlap)

    try:
        enc = tiktoken.get_encoding(encoding_name)
    except Exception:
        return _char_chunk_fallback(text, chunk_size, overlap)

    return _chunk_with_encoder(text, enc, chunk_size, overlap, token_chunk_size, token_overlap)


def chunk_text_batch(
    texts: List[str],
    chunk_size: int = 500,
    overlap: int = 50,
    token_chunk_size: Optional[int] = 380,
    token_overlap: Optional[int] = 60,
    encoding_name: str = "cl100k_base",
) -> List[List[str]]:
    """Batch process multiple texts for better efficiency."""
    if not texts:
        return []
    
    # Pre-load encoder once for batch processing
    enc = None
    if tiktoken is not None and token_chunk_size is not None:
        try:
            enc = tiktoken.get_encoding(encoding_name)
        except Exception:
            pass
    
    results = []
    for text in texts:
        if enc is not None:
            # Use optimized path with pre-loaded encoder
            results.append(_chunk_with_encoder(text, enc, chunk_size, overlap, token_chunk_size, token_overlap))
        else:
            results.append(_char_chunk_fallback(text, chunk_size, overlap))
    
    return results