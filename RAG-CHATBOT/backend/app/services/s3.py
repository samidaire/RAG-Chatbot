import logging
from typing import BinaryIO

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_session = None
_s3_client = None


def is_s3_enabled() -> bool:
    settings = get_settings()
    return all([
        settings.aws_access_key_id,
        settings.aws_secret_access_key,
        settings.aws_region,
        settings.s3_bucket,
    ])


def _get_client():
    global _session, _s3_client
    if _s3_client:
        return _s3_client
    settings = get_settings()
    if not is_s3_enabled():
        raise HTTPException(status_code=503, detail={"error": "s3_config_missing", "message": "S3 credentials or bucket not fully configured"})
    _session = boto3.session.Session(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )
    logger.info("Initializing S3 client region=%s bucket=%s", settings.aws_region, settings.s3_bucket)
    _s3_client = _session.client("s3")
    return _s3_client


def upload_bytes(data: bytes, key: str, content_type: str | None = None) -> str:
    client = _get_client()
    settings = get_settings()
    extra = {"ContentType": content_type} if content_type else {}
    try:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, **extra)
    except (BotoCoreError, ClientError) as e:  # pragma: no cover
        logger.exception("Failed to upload to S3")
        raise HTTPException(status_code=502, detail={"error": "s3_upload_failed", "message": str(e)}) from e
    return key


def upload_fileobj(fileobj: BinaryIO, key: str, content_type: str | None = None) -> str:
    client = _get_client()
    settings = get_settings()
    extra = {"ContentType": content_type} if content_type else {}
    try:
        client.upload_fileobj(fileobj, settings.s3_bucket, key, ExtraArgs=extra if extra else None)
    except (BotoCoreError, ClientError) as e:  # pragma: no cover
        logger.exception("Failed to upload fileobj to S3")
        raise HTTPException(status_code=502, detail={"error": "s3_upload_failed", "message": str(e)}) from e
    return key


def get_object_bytes(key: str) -> bytes:
    client = _get_client()
    settings = get_settings()
    try:
        resp = client.get_object(Bucket=settings.s3_bucket, Key=key)
        return resp["Body"].read()
    except (BotoCoreError, ClientError) as e:  # pragma: no cover
        logger.exception("Failed to download from S3")
        raise HTTPException(status_code=502, detail={"error": "s3_download_failed", "message": str(e)}) from e


def delete_object(key: str) -> None:
    client = _get_client()
    settings = get_settings()
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError) as e:  # pragma: no cover
        logger.exception("Failed deleting S3 object %s", key)
        raise HTTPException(status_code=502, detail={"error": "s3_delete_failed", "message": str(e)}) from e


def delete_prefix(prefix: str) -> int:
    """Delete all objects under a prefix. Returns number deleted."""
    client = _get_client()
    settings = get_settings()
    try:
        paginator = client.get_paginator('list_objects_v2')
        deleted = 0
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
            contents = page.get('Contents', [])
            if not contents:
                continue
            objs = [{'Key': o['Key']} for o in contents]
            client.delete_objects(Bucket=settings.s3_bucket, Delete={'Objects': objs})
            deleted += len(objs)
        return deleted
    except (BotoCoreError, ClientError) as e:  # pragma: no cover
        logger.exception("Failed deleting S3 prefix %s", prefix)
        raise HTTPException(status_code=502, detail={"error": "s3_prefix_delete_failed", "message": str(e)}) from e
