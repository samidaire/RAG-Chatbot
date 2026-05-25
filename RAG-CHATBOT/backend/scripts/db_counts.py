import asyncio
import os
import sys

# Ensure backend root added to sys.path when running directly
CURRENT_DIR = os.path.dirname(__file__)
BACKEND_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.db.mongo import init_mongo, ensure_indexes, documents_col, chunks_col, conversations_col, messages_col, upload_jobs_col  # noqa: E402


async def main():
    await init_mongo()
    await ensure_indexes()
    print("documents:", await documents_col().count_documents({}))
    print("chunks:", await chunks_col().count_documents({}))
    print("conversations:", await conversations_col().count_documents({}))
    print("messages:", await messages_col().count_documents({}))
    print("upload_jobs:", await upload_jobs_col().count_documents({}))


if __name__ == "__main__":
    asyncio.run(main())
