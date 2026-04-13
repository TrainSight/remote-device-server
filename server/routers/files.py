from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from server.auth import verify_api_key
from server.config import settings

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    username: str = Depends(verify_api_key),
) -> Dict[str, str]:
    """Upload a tarball for task execution. Stored inside the user's workspace."""
    client_ws = settings.get_client_workspace(username)
    upload_id = uuid.uuid4().hex[:12]
    dest = client_ws / f"{upload_id}.tar.gz"
    with open(dest, "wb") as f:
        chunk = await file.read(1024 * 1024)
        while chunk:
            f.write(chunk)
            chunk = await file.read(1024 * 1024)
    return {"upload_id": upload_id, "filename": file.filename or "unknown"}


@router.get("/download")
async def download_file(
    path: str,
    username: str = Depends(verify_api_key),
) -> FileResponse:
    """Download a file from the server.

    If `path` is relative it is resolved against the user's workspace,
    otherwise the absolute path is used as-is (no jail enforcement here,
    the server admin controls access via the API key).
    """
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = settings.get_client_workspace(username) / file_path
    file_path = file_path.resolve()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path, filename=file_path.name)
