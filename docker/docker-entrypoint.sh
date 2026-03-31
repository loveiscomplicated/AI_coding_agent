#!/bin/sh
# workspace 안에 requirements.txt가 있으면 먼저 설치
if [ -f /workspace/requirements.txt ]; then
    pip install --no-cache-dir -q -r /workspace/requirements.txt
fi

# src/__init__.py 가 없으면 main 레포 구조 — workspace 루트를 src 패키지로 매핑
# (태스크 브랜치 테스트는 src/__init__.py 가 있으므로 이 분기에 진입하지 않음)
if [ ! -f /workspace/src/__init__.py ]; then
    mkdir -p /tmp/src
    printf '__path__ = ["/workspace"]\n' > /tmp/src/__init__.py
    PYTHONPATH="/tmp:${PYTHONPATH:-}"
    export PYTHONPATH
fi

# pytest 실행: 인자가 없으면 tests/ 디렉토리 자동 탐색
exec python -m pytest --tb=short -q --no-header "$@"
