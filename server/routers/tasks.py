from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from server.auth import verify_api_key
from server.config import settings
from server.db import get_db
from server.models import (
    LogChunk,
    TaskCreate,
    TaskInfo,
    TaskListResponse,
    TaskStatus,
)
from server.services.log_store import log_store
from server.services.task_manager import task_manager

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskInfo)
async def create_task(
    task: TaskCreate,
    username: str = Depends(verify_api_key),
) -> TaskInfo:
    # Determine working directory:
    # explicit > upload_id extract dir > user's personal workspace
    user_ws = settings.get_client_workspace(username)

    working_dir = task.working_dir or str(user_ws)

    if task.upload_id:
        upload_path = user_ws / f"{task.upload_id}.tar.gz"
        if upload_path.exists():
            extract_dir = user_ws / task.upload_id
            extract_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(upload_path, "r:gz") as tar:
                tar.extractall(extract_dir)
            working_dir = str(extract_dir)
            upload_path.unlink()

    task_id = await task_manager.submit(task.command, task.conda_env, working_dir)
    db = await get_db()
    # Persist username on the task row
    await db.execute(
        "UPDATE tasks SET client_id=? WHERE id=?", (username, task_id)
    )
    await db.commit()
    row = await db.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    data = await row.fetchone()
    return _row_to_task(data)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    limit: int = 50,
    offset: int = 0,
    username: str = Depends(verify_api_key),
) -> TaskListResponse:
    db = await get_db()
    rows = await db.execute(
        "SELECT * FROM tasks WHERE client_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (username, limit, offset),
    )
    tasks = [_row_to_task(row) async for row in rows]
    count_row = await db.execute(
        "SELECT COUNT(*) FROM tasks WHERE client_id=?", (username,)
    )
    total = (await count_row.fetchone())[0]
    return TaskListResponse(tasks=tasks, total=total)


@router.get("/{task_id}", response_model=TaskInfo)
async def get_task(
    task_id: str,
    username: str = Depends(verify_api_key),
) -> TaskInfo:
    db = await get_db()
    row = await db.execute(
        "SELECT * FROM tasks WHERE id=? AND client_id=?", (task_id, username)
    )
    data = await row.fetchone()
    if not data:
        raise HTTPException(404, "Task not found")
    return _row_to_task(data)


@router.delete("/{task_id}")
async def cancel_task(
    task_id: str,
    username: str = Depends(verify_api_key),
) -> Dict[str, str]:
    # Verify ownership before cancelling
    db = await get_db()
    row = await db.execute(
        "SELECT id FROM tasks WHERE id=? AND client_id=?", (task_id, username)
    )
    if not await row.fetchone():
        raise HTTPException(404, "Task not found")
    success = await task_manager.cancel(task_id)
    return {"status": "cancelled" if success else "not_running"}


@router.get("/{task_id}/logs", response_model=LogChunk)
async def get_logs(
    task_id: str,
    offset: int = 0,
    username: str = Depends(verify_api_key),
) -> LogChunk:
    # Verify ownership
    db = await get_db()
    row = await db.execute(
        "SELECT id FROM tasks WHERE id=? AND client_id=?", (task_id, username)
    )
    if not await row.fetchone():
        raise HTTPException(404, "Task not found")
    data, new_offset = await log_store.read(task_id, offset)
    return LogChunk(data=data, offset=new_offset)


@router.websocket("/{task_id}/logs/ws")
async def logs_websocket(websocket: WebSocket, task_id: str):
    await websocket.accept()
    try:
        from server.auth import _key_to_client_id

        api_key = websocket.headers.get("x-api-key")
        if api_key != settings.api_key:
            await websocket.close(code=1008, reason="Invalid API key")
            return

        from server.auth import _key_to_fallback_username
        username = _key_to_fallback_username(api_key)

        # Verify task ownership
        db = await get_db()
        row = await db.execute(
            "SELECT id FROM tasks WHERE id=? AND client_id=?", (task_id, username)
        )
        if not await row.fetchone():
            await websocket.close(code=1008, reason="Task not found")
            return

        existing, _ = await log_store.read(task_id, 0)
        if existing:
            await websocket.send_text(existing)

        q = log_store.subscribe(task_id)
        try:
            while True:
                line = await q.get()
                if line is None:
                    break
                await websocket.send_text(line)
        finally:
            log_store.unsubscribe(task_id, q)
    except WebSocketDisconnect:
        pass


def _row_to_task(row) -> TaskInfo:
    return TaskInfo(
        id=row["id"],
        command=row["command"],
        conda_env=row["conda_env"],
        status=TaskStatus(row["status"]),
        exit_code=row["exit_code"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        working_dir=row["working_dir"],
    )
