import asyncio
import os
import sys
import math

CURRENT_DIR = os.path.dirname(__file__)
BACKEND_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.services.embedding import embed_texts  # noqa: E402
from app.core.settings import get_settings  # noqa: E402


async def main():
    settings = get_settings()
    sample = ["Quick embedding connectivity check."]
    try:
        vectors = await embed_texts(sample)
    except Exception as e:  # noqa: BLE001
        print("ERROR", repr(e))
        return
    if not vectors:
        print("NO_VECTORS_RETURNED")
        return
    vec = vectors[0]
    dim = len(vec)
    # Simple stats
    finite = sum(1 for x in vec if math.isfinite(float(x)))
    print("MODEL:", settings.embed_model)
    print("DIMENSION:", dim)
    print("FINITE_VALUES:", finite)
    print("HEAD:", list(map(float, vec[:8])))


if __name__ == "__main__":
    asyncio.run(main())
