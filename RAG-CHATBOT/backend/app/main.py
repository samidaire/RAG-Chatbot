from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.chat import router as chat_router
from app.api.upload import router as upload_router
from app.api.documents import router as documents_router
from app.api.conversations import router as conversations_router
from app.api.retrieve import router as retrieve_router
from app.core.settings import get_settings
from app.db.mongo import init_mongo, close_mongo, ensure_indexes

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(upload_router)
app.include_router(documents_router)
app.include_router(conversations_router)
app.include_router(retrieve_router)

@app.on_event("startup")
async def _startup():
    await init_mongo()
    await ensure_indexes()

@app.on_event("shutdown")
async def _shutdown():
    await close_mongo()

@app.get("/")
async def root():
    return {"app": settings.app_name, "message": "RAG MVP backend"}
