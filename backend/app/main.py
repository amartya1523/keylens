from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import api, proxy
from .config import CORS_ORIGINS
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    await proxy.close_client()


app = FastAPI(title="KeyLens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-api-key", "anthropic-version",
                   "x-stainless-lang", "x-stainless-package-version", "x-stainless-os",
                   "x-stainless-arch", "x-stainless-runtime", "x-stainless-runtime-version",
                   "x-stainless-retry-count", "x-stainless-timeout", "x-stainless-async"],
    expose_headers=["x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
                    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-request-id"],
)
app.include_router(api.router)
app.include_router(proxy.router)


@app.get("/health")
def health():
    return {"ok": True}
