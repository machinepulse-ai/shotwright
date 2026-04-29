"""Shotwright API — FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import close_db, connect_db
from app.routers import admin, agent, containers, projects, sessions, streaming
from app.services.agent_runtime import runtime_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    await runtime_manager.ensure_repo_skills_available()
    yield
    await runtime_manager.shutdown()
    await close_db()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/api/uploads", StaticFiles(directory=settings.upload_dir, check_dir=False), name="uploads")

# --- Routers ---
app.include_router(sessions.router, prefix="/api")
app.include_router(containers.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(streaming.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": settings.app_name}
