"""
backend/routers/utils.py — 로컬 유틸리티 엔드포인트

GET /api/utils/browse?type=folder   → macOS 파인더에서 폴더 선택
GET /api/utils/browse?type=file     → macOS 파인더에서 파일 선택
"""

import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


def _resolve_initial(initial: str) -> str:
    """
    초기 경로를 절대경로로 변환한다.
    절대경로가 아니거나 존재하지 않으면 홈 디렉토리를 반환한다.
    """
    p = Path(initial).expanduser()
    if not p.is_absolute():
        return str(Path.home())
    # 존재하는 상위 디렉토리까지 올라감
    while p != p.parent:
        if p.exists():
            return str(p)
        p = p.parent
    return str(Path.home())


@router.get("/utils/browse")
def browse_path(
    type: str = Query("folder", pattern="^(folder|file)$"),
    initial: str = Query("~"),
):
    """
    macOS osascript를 통해 네이티브 파인더 다이얼로그를 열고
    선택된 경로를 반환한다.
    """
    expanded = _resolve_initial(initial)

    if type == "folder":
        script = (
            f'POSIX path of (choose folder '
            f'with prompt "폴더 선택" '
            f'default location POSIX file "{expanded}")'
        )
    else:
        script = (
            f'POSIX path of (choose file '
            f'with prompt "파일 선택" '
            f'default location POSIX file "{expanded}")'
        )

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # 사용자가 취소하면 returncode=1, stderr에 "User canceled" 포함
        stderr = result.stderr.strip()
        if "canceled" in stderr.lower() or "cancelled" in stderr.lower():
            return {"path": None, "cancelled": True}
        raise HTTPException(status_code=500, detail=stderr or "osascript 실행 실패")

    path = result.stdout.strip().rstrip("/")
    return {"path": path, "cancelled": False}
