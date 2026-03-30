터미널 1 — 백엔드:
  source .venv/bin/activate
  uvicorn backend.main:app --reload --port 8000

  터미널 2 — 프론트엔드:
  cd frontend && npm run dev

  .env (프로젝트 루트)에 ANTHROPIC_API_KEY=sk-ant-...가 있어야 합니다.