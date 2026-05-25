#!/usr/bin/env python
"""Environment readiness checker for the RAG backend.

Usage:
  python scripts/check_env.py

Outputs a table of required/optional variables and whether they are set.
Exits non-zero if any required variable is missing.
"""
from __future__ import annotations
import os
import sys
from textwrap import shorten

# Attempt to load .env manually since Pydantic auto-loading only occurs inside app context.
def _load_dotenv():
    # Try python-dotenv if available; fall back to simple parser
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass
    # Minimal fallback parser
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())
    except Exception:
        pass

_load_dotenv()

REQUIRED = [
    "OPENAI_API_KEY",
    "PINECONE_API_KEY",
    "PINECONE_INDEX",
    "MONGO_URI",
    "MONGO_DB",
]
OPTIONAL = [
    "EMBED_MODEL",
    "LLM_MODEL",
    "PINECONE_CLOUD",
    "PINECONE_REGION",
    "PINECONE_ENV",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "S3_BUCKET",
    "TOP_K",
]

def status(var: str):
    val = os.getenv(var)
    return (val is not None and val != ""), val

missing = []
rows = []
for var in REQUIRED:
    ok, val = status(var)
    if not ok:
        missing.append(var)
    rows.append((var, "required", "SET" if ok else "MISSING", shorten(val or "", width=50, placeholder="…")))

for var in OPTIONAL:
    ok, val = status(var)
    rows.append((var, "optional", "SET" if ok else "-", shorten(val or "", width=50, placeholder="…")))

w1 = max(len(r[0]) for r in rows)
print("Variable".ljust(w1), "Type     Status   Value")
print("-" * (w1 + 25))
for name, kind, st, val in rows:
    print(name.ljust(w1), kind.ljust(8), st.ljust(8), val)

if missing:
    print("\n[ERROR] Missing required variables:", ", ".join(missing))
    sys.exit(1)

print("\n[OK] All required variables present.")

# Additional S3 completeness check
s3_set = all(os.getenv(v) for v in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET"])
if s3_set:
    print("[INFO] S3 configuration: COMPLETE")
else:
    partial = any(os.getenv(v) for v in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET"])
    if partial:
        print("[WARN] S3 configuration partially set (will be ignored unless complete).")

