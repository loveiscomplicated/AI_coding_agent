"""
backend/main.py — FastAPI 앱 진입점

실행:
  uvicorn backend.main:app --reload --port 8000
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


# ── 폴링성 엔드포인트 access 로그 억제 ─────────────────────────────────────────
class _SuppressPollingLog(logging.Filter):
    _SUPPRESS = {'/api/pipeline/jobs'}

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in self._SUPPRESS)

logging.getLogger('uvicorn.access').addFilter(_SuppressPollingLog())

from backend.routers import chat, dashboard, discord_router, health, pipeline, reports, tasks, utils

app = FastAPI(title="Multi-Agent Dev System API", version="0.1.0")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logging.getLogger(__name__).error("RequestValidationError on %s: %s", request.url, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# 로컬 프론트엔드(Vite)와의 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(chat.router, prefix="/api/chat")
app.include_router(tasks.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(discord_router.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(utils.router, prefix="/api")
