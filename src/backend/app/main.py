"""Shotwright API — FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import close_db, connect_db
from app.routers import admin, agent, containers, projects, sessions, streaming


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
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
