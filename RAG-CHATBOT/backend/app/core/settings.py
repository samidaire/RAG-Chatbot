from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    app_name: str = "rag-mvp"
    environment: str = "dev"
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    embed_model: str = Field(default="text-embedding-3-small", alias="EMBED_MODEL")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    mongo_uri: str = Field(default="mongodb://localhost:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="ragdb", alias="MONGO_DB")
    # retrieval
    top_k: int = 5
    # S3
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    # Pinecone
    pinecone_api_key: str | None = Field(default=None, alias="PINECONE_API_KEY")
    pinecone_environment: str | None = Field(default=None, alias="PINECONE_ENV")
    pinecone_index: str | None = Field(default=None, alias="PINECONE_INDEX")
    pinecone_cloud: str | None = Field(default=None, alias="PINECONE_CLOUD")
    pinecone_region: str | None = Field(default=None, alias="PINECONE_REGION")
    # File staging (local temp storage for batch uploads)
    staging_dir: str = Field(default="storage/uploads", alias="STAGING_DIR")

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]
