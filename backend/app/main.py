from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="Custom-Mojo Options Analytics & Strategy Engine",
    description=(
        "Research Mode analytics (option chain, OI, Max Pain, PCR, IV, Greeks, straddle) "
        "and Strategy Command Mode (constraint-solving multi-leg strategy discovery, "
        "backtesting, and paper execution) for Indian index & stock F&O."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
