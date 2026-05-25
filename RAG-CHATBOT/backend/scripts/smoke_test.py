#!/usr/bin/env python
"""Simple smoke test for the RAG backend.

Usage:
  python scripts/smoke_test.py --base-url http://localhost:8000 --pdf sample.pdf

If no --pdf is provided, the script will attempt to generate a small dummy PDF in-memory.

Steps:
 1. Health check
 2. Upload PDF (direct to S3 if configured)
 3. Poll upload status until 'uploaded'
 4. Trigger processing
 5. Poll until completed
 6. Ask a chat question
 7. Fetch history

Exits non-zero on failure.
"""
from __future__ import annotations
import argparse
import sys
import time
import uuid
import io
from typing import Optional

import httpx

DUMMY_PDF_TEXT = "This is a simple test PDF containing latency information and architecture notes. " \
    "Latency is the time difference between request and response."  # small content


def make_dummy_pdf() -> bytes:
    try:
        from reportlab.pdfgen import canvas  # type: ignore
        from reportlab.lib.pagesizes import letter  # type: ignore
    except Exception:
        # fallback plain bytes so upload still works (will parse as empty maybe)
        return DUMMY_PDF_TEXT.encode()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, DUMMY_PDF_TEXT[:100])
    c.drawString(72, 700, DUMMY_PDF_TEXT[100:200])
    c.showPage()
    c.save()
    return buf.getvalue()


def assert_status(resp: httpx.Response, step: str):
    if resp.status_code >= 400:
        print(f"[FAIL] {step}: status={resp.status_code} body={resp.text}")
        sys.exit(1)


def wait_for_status(client: httpx.Client, base_url: str, job_id: str, target: str, timeout: int = 120):
    start = time.time()
    while time.time() - start < timeout:
        r = client.get(f"{base_url}/upload-status/{job_id}")
        if r.status_code >= 400:
            print(f"[FAIL] polling upload-status: {r.status_code} {r.text}")
            sys.exit(1)
        data = r.json()
        status = data.get("status")
        print(f"  poll status={status} processed={data.get('processed_files')} errors={len(data.get('errors', []))}")
        if status == target:
            return data
        time.sleep(1.5)
    print(f"[FAIL] timeout waiting for status {target}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL of running API")
    parser.add_argument("--pdf", help="Path to a PDF to upload")
    parser.add_argument("--question", default="What does the test PDF mention about latency?", help="Chat question to ask")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    print("[INFO] Starting smoke test against", args.base_url)

    with httpx.Client(timeout=30.0) as client:
        # 1. Health
        r = client.get(f"{args.base_url}/health")
        assert_status(r, "health")
        print("[OK] Health:", r.json())

        # Prepare file
        pdf_bytes: bytes
        filename: str
        if args.pdf:
            try:
                pdf_bytes = open(args.pdf, "rb").read()
                filename = args.pdf.split("/")[-1]
            except Exception as e:  # noqa: BLE001
                print("[FAIL] Unable to read provided PDF:", e)
                sys.exit(1)
        else:
            pdf_bytes = make_dummy_pdf()
            filename = f"dummy_{uuid.uuid4().hex[:8]}.pdf"
            print(f"[INFO] Generated dummy PDF {filename} (size={len(pdf_bytes)} bytes)")

        # 2. Upload
        files = {"files": (filename, pdf_bytes, "application/pdf")}
        r = client.post(f"{args.base_url}/upload-files", files=files)
        assert_status(r, "upload-files")
        job_id = r.json().get("job_id")
        if not job_id:
            print("[FAIL] No job_id returned from upload")
            sys.exit(1)
        print("[OK] Uploaded job_id=", job_id)

        # 3. Wait for uploaded
        job_data = wait_for_status(client, args.base_url, job_id, target="uploaded")
        docs = job_data.get("documents", [])
        if not docs:
            print("[FAIL] No documents metadata after upload")
            sys.exit(1)
        print(f"[OK] Upload stage complete docs={len(docs)}")

        # 4. Trigger processing
        r = client.post(f"{args.base_url}/process-documents", data={"job_id": job_id})
        assert_status(r, "process-documents")
        print("[OK] Processing started")

        # 5. Wait for completed
        final_job = wait_for_status(client, args.base_url, job_id, target="completed")
        ingested = final_job.get("ingested", 0)
        print(f"[OK] Processing complete ingested={ ingested }")
        if ingested == 0:
            print("[WARN] No new documents ingested (may be duplicates). Continuing to chat test.")

        # 6. Chat
        payload = {"query": args.question, "top_k": args.top_k}
        r = client.post(f"{args.base_url}/chat", json=payload)
        assert_status(r, "chat")
        chat_resp = r.json()
        answer = chat_resp.get("answer")
        citations = chat_resp.get("citations", [])
        conv_id = chat_resp.get("conversation_id")
        print(f"[OK] Chat answer length={len(answer or '')} citations={len(citations)} conversation_id={conv_id}")
        if not answer:
            print("[FAIL] Empty answer returned")
            sys.exit(1)

        # 7. History
        r = client.get(f"{args.base_url}/chat/history", params={"conversation_id": conv_id})
        assert_status(r, "chat/history")
        hist = r.json()
        print(f"[OK] History messages={len(hist.get('messages', []))}")

    print("[SUCCESS] Smoke test completed.")


if __name__ == "__main__":
    main()
