import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.db import init_db
from app.routers import (
    calls as calls_router,
)
from app.routers import (
    carriers as carriers_router,
)
from app.routers import (
    loads as loads_router,
)
from app.routers import (
    metrics as metrics_router,
)
from app.routers import (
    negotiations as negotiations_router,
)

load_dotenv()

VERSION = "0.1.0"


def _rate_limit_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key, default_limits=["60/minute"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


docs_enabled = os.getenv("ENABLE_DOCS", "false").lower() == "true"

app = FastAPI(
    title="acme-carrier-api",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

allowed_origins = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION}


app.include_router(loads_router.router)
app.include_router(carriers_router.router)
app.include_router(negotiations_router.router)
app.include_router(calls_router.router)
app.include_router(metrics_router.router)
