from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.config import settings
from server.db import close_db, get_db
from server.routers import envs, files, monitor, tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_db()
    yield
    await close_db()


app = FastAPI(title="Remote Device Server", version="0.1.0", lifespan=lifespan)

app.include_router(tasks.router)
app.include_router(files.router)
app.include_router(monitor.router)
app.include_router(envs.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def run():
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    run()
