"""Text extraction and chunking utilities. No external state — pure functions."""

import os
from functools import lru_cache
from typing import List

import tiktoken

CHUNK_TOKENS = 400          # ~300 words — fits one full resume section
CHUNK_OVERLAP_TOKENS = 60   # ~45 words carried into the next chunk for context continuity


@lru_cache(maxsize=1)
def _encoder():
    # Shared by ada-002 embeddings and GPT-4 family
    return tiktoken.get_encoding("cl100k_base")


def chunk_text(
    text: str,
    max_tokens: int = CHUNK_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> List[str]:
    """Split text into overlapping token-bounded chunks suitable for embedding."""
    enc = _encoder()

    def _atoms(t: str, seps: List[str]) -> List[str]:
        """Recursively split on separators until every piece fits within max_tokens."""
        if len(enc.encode(t)) <= max_tokens:
            return [t] if t.strip() else []
        sep, *rest = seps
        result: List[str] = []
        for part in t.split(sep):
            if not part.strip():
                continue
            if len(enc.encode(part)) <= max_tokens:
                result.append(part)
            elif rest:
                result.extend(_atoms(part, rest))
            else:
                # Hard token split as last resort
                toks = enc.encode(part)
                for i in range(0, len(toks), max_tokens):
                    decoded = enc.decode(toks[i: i + max_tokens]).strip()
                    if decoded:
                        result.append(decoded)
        return result

    atoms = _atoms(text, ["\n\n", "\n", ". ", " "])
    if not atoms:
        return []

    # Greedily merge atoms into chunks; seed each new chunk with overlap tokens
    chunks: List[str] = []
    cur_toks: List[int] = []

    for atom in atoms:
        a_toks = enc.encode(atom)
        if cur_toks and len(cur_toks) + len(a_toks) > max_tokens:
            chunks.append(enc.decode(cur_toks).strip())
            cur_toks = cur_toks[-overlap:] + a_toks
        else:
            cur_toks.extend(a_toks)

    if cur_toks:
        chunks.append(enc.decode(cur_toks).strip())

    return [c for c in chunks if c.strip()]


def extract_text(file_name: str, data: bytes) -> str:
    """Extract plain text from PDF, DOCX, or UTF-8 bytes.

    Uses os.path.basename so Windows full paths (e.g. C:\\Users\\...\\cv.pdf)
    are handled correctly when the filename comes from an uploaded file object.
    """
    # Strip drive path — safe on both Windows and Mac
    file_name = os.path.basename(file_name)
    ext = file_name.lower().rsplit(".", 1)[-1]

    if ext == "pdf":
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    if ext in ("docx", "doc"):
        import io
        from docx import Document
        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)

    # Plain text — handles \r\n line endings from Windows files
    return data.decode("utf-8", errors="replace")
