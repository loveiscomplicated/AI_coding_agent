#!/bin/sh
# workspace 안에 requirements.txt가 있으면 먼저 설치
if [ -f /workspace/requirements.txt ]; then
    pip install --no-cache-dir -q -r /workspace/requirements.txt
fi

# pytest 실행: 인자가 없으면 tests/ 디렉토리 자동 탐색
exec python -m pytest --tb=short -q --no-header "$@"
