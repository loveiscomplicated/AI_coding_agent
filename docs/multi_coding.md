터미널 1 — 백엔드:
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000

터미널 2 — 프론트엔드:
cd frontend && npm run dev

도커 리빌드:
docker build -f docker/Dockerfile.test -t ai-coding-agent-test-runner .                                