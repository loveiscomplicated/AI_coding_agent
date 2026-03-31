"""
backend/routers/utils.py — 로컬 유틸리티 엔드포인트

GET /api/utils/browse?type=folder   → macOS 파인더에서 폴더 선택
GET /api/utils/browse?type=file     → macOS 파인더에서 파일 선택
"""

import subprocess

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/utils/browse")
def browse_path(
    type: str = Query("folder", pattern="^(folder|file)$"),
    initial: str = Query("~"),
):
    """
    macOS osascript를 통해 네이티브 파인더 다이얼로그를 열고
    선택된 경로를 반환한다.
    """
    initial_expanded = initial.replace("~", "$HOME")
    # shell=True 로 $HOME 확장
    expanded = subprocess.run(
        f'echo {initial_expanded}', shell=True, capture_output=True, text=True
    ).stdout.strip() or "/"

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
