import asyncio
import os
import sys
import json
import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


async def post_json(path: str, payload: dict):
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{BASE_URL}{path}", json=payload)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return r.status_code, data


async def run():
    steps = []
    # 1. Vague query with min_score triggers clarification
    status, data = await post_json("/chat", {"query": "what is this?", "top_k": 5, "min_score": 0.30})
    steps.append(("clarification", status, data))
    conv = data.get("conversation_id")
    # 2. Ask for API endpoints (concise default)
    status, data2 = await post_json("/chat", {"query": "List the API endpoints.", "conversation_id": conv, "top_k": 5, "min_score": 0.30})
    steps.append(("api_endpoints", status, data2))
    # 3. Detailed mode for architecture
    status, data3 = await post_json("/chat", {"query": "Provide a detailed architecture overview.", "conversation_id": conv, "top_k": 5, "answer_mode": "detailed", "max_citations": 3})
    steps.append(("architecture_detailed", status, data3))
    # 4. Follow-up using suggestion
    suggestion = None
    for f in data2.get("followups", []):
        if "architecture" in f.lower():
            suggestion = f
            break
    if suggestion:
        status, data4 = await post_json("/chat", {"query": suggestion, "conversation_id": conv, "top_k": 5})
        steps.append(("followup_architecture", status, data4))

    for label, st, payload in steps:
        print("==", label, st)
        minimal = {k: payload.get(k) for k in ["answer", "clarification", "followups", "used_citations", "answer_mode", "reason"] if k in payload}
        print(json.dumps(minimal, indent=2)[:800])


if __name__ == "__main__":
    asyncio.run(run())
