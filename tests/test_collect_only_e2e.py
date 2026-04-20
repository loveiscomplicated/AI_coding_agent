"""
tests/test_collect_only_e2e.py

--collect-only 사전 검사 및 다국어 [NO_TESTS_COLLECTED] 감지의 E2E 테스트.
실제 Docker 데몬을 사용하므로, 데몬이 실행 중이지 않으면 모듈 단위로 skip.

실행:
    pytest tests/test_collect_only_e2e.py -v

    # Docker 없는 환경에서는 자동 skip:
    #   SKIPPED [3] Docker 데몬이 실행 중이지 않음
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from docker.runner import DockerTestRunner


def _docker_available() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_DOCKER_OK = _docker_available()

docker_required = pytest.mark.skipif(
    not _DOCKER_OK, reason="Docker 데몬이 실행 중이지 않음 — E2E 스킵",
)


# ── 실제 "오버헤드 < 2초" 검증 (Docker 불필요) ─────────────────────────────
# 컨테이너 안에서 우리가 추가한 것은 `pytest --collect-only -q` 한 번뿐이다.
# 따라서 추가 오버헤드 = (collect-only 호출 시간) 자체이다. 이를 직접 측정해
# 3회 평균이 < 2초인지 assert 한다. Docker 환경 유무와 무관.


def test_collect_only_overhead_under_2_seconds_unit(tmp_path):
    """
    entrypoint 의 collect-only 게이트가 정상 태스크에 추가하는 오버헤드는
    `python -m pytest --collect-only -q` 한 번의 실행 시간과 동일하다.
    3회 반복 평균을 측정하여 사용자 요구(<2s)를 직접 검증한다.

    보수적으로 절대 임계 2.0s 를 assert 하여, 향후 플러그인 추가 등으로
    collect 가 느려지면 회귀로 잡는다.
    """
    (tmp_path / "tests").mkdir()
    # 현실적 크기: 10개 테스트 함수 (단일 모듈)
    body = "\n".join(f"def test_case_{i}(): assert True" for i in range(10))
    (tmp_path / "tests" / "test_many.py").write_text(body + "\n")

    # 웜업: import/컴파일 캐시 효과 제거
    subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=tmp_path, capture_output=True, timeout=30,
    )

    elapsed = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=tmp_path, capture_output=True, timeout=30,
        )
        elapsed.append(time.perf_counter() - t0)
        assert r.returncode == 0, f"collect 실패: {r.stdout!r}"
        assert b"10 tests collected" in r.stdout or b"collected 10 items" in r.stdout

    avg = sum(elapsed) / len(elapsed)
    print(
        f"\n[overhead:unit] --collect-only 3-run avg={avg:.3f}s "
        f"runs={[f'{t:.3f}s' for t in elapsed]}"
    )
    # 사용자 요구: 정상 태스크에서 추가 오버헤드 < 2초.
    assert avg < 2.0, (
        f"collect-only 추가 오버헤드 평균 {avg:.2f}s — 2초 목표 초과. "
        f"runs={elapsed}"
    )


def test_collect_only_overhead_vs_baseline_unit(tmp_path):
    """
    더 엄격한 증빙: (collect-only 다음 pytest) vs (pytest 단독) 의 시간차
    = 순수 오버헤드. 이 델타가 2초 미만임을 직접 확인.
    """
    (tmp_path / "tests").mkdir()
    body = "\n".join(f"def test_case_{i}(): assert True" for i in range(10))
    (tmp_path / "tests" / "test_many.py").write_text(body + "\n")

    def _time_cmd(args):
        t0 = time.perf_counter()
        r = subprocess.run(
            args, cwd=tmp_path, capture_output=True, timeout=30,
        )
        return time.perf_counter() - t0, r.returncode

    # 웜업
    _time_cmd([sys.executable, "-m", "pytest", "-q", "--tb=no"])

    # baseline: pytest 한 번
    baseline = []
    for _ in range(3):
        t, rc = _time_cmd([sys.executable, "-m", "pytest", "-q", "--tb=no"])
        baseline.append(t)
        assert rc == 0

    # with gate: pytest --collect-only 한 번 + pytest 한 번
    with_gate = []
    for _ in range(3):
        t_c, rc_c = _time_cmd(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"]
        )
        t_r, rc_r = _time_cmd([sys.executable, "-m", "pytest", "-q", "--tb=no"])
        with_gate.append(t_c + t_r)
        assert rc_c == 0 and rc_r == 0

    base_avg = sum(baseline) / 3
    gate_avg = sum(with_gate) / 3
    delta = gate_avg - base_avg
    print(
        f"\n[overhead:delta] baseline={base_avg:.3f}s  with_gate={gate_avg:.3f}s  "
        f"delta={delta:.3f}s"
    )
    assert delta < 2.0, (
        f"collect-only 게이트 추가 오버헤드 {delta:.3f}s — 2초 목표 초과.\n"
        f"baseline runs={baseline}\n  with-gate runs={with_gate}"
    )


# ── Docker E2E 테스트 (데몬 있을 때만) ───────────────────────────────────────
# 모듈-레벨 pytestmark 는 쓰지 않는다 (위의 unit 오버헤드 테스트까지 스킵시키면
# 안 되므로). Docker 필요한 테스트에만 @docker_required 데코레이터를 건다.


# ── task-025 재현: tests/ 디렉토리만 있고 test_*.py 없음 ──────────────────────


@docker_required
def test_task_025_repro_returns_no_tests_collected(tmp_path):
    """
    task-025 재현: tests/ 디렉토리에 보조 파일만 있고 `test_*.py` 가 없는
    워크스페이스. 러너는 재시도 루프 없이 즉시 [NO_TESTS_COLLECTED] 를 반환해야
    한다.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "helper.py").write_text("# 보조 모듈 (테스트 아님)\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def f(): return 1\n")

    runner = DockerTestRunner(timeout=60)
    result = runner.run(tmp_path, language="python", test_framework="pytest")

    assert result.passed is False
    assert result.returncode == 71, f"unexpected rc={result.returncode}\n{result.stdout}"
    assert result.failure_reason == "[NO_TESTS_COLLECTED]"


@docker_required
def test_collection_error_on_import_failure(tmp_path):
    """import 에러가 있는 test 파일 → [COLLECTION_ERROR] 로 즉시 분기."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_broken.py").write_text(
        "import nonexistent_module_xyz\n\n"
        "def test_ok():\n"
        "    assert True\n"
    )

    runner = DockerTestRunner(timeout=60)
    result = runner.run(tmp_path, language="python", test_framework="pytest")

    assert result.passed is False
    assert result.returncode == 70, f"unexpected rc={result.returncode}\n{result.stdout}"
    assert result.failure_reason == "[COLLECTION_ERROR]"


@docker_required
def test_conftest_error_fails_entire_collection(tmp_path):
    """
    부분 collect 실패(conftest.py 에러) 에 대한 명시적 결정:
    **전체를 COLLECTION_ERROR 로 처리**한다. 이유는 conftest 가 전역 fixture·훅
    등록 지점이어서 해당 오류는 다른 테스트 실행에도 영향을 주므로, 부분 통과를
    허용하면 무작위 비결정적 실패가 양산된다.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(
        "import nonexistent_fixture_module\n"
    )
    # conftest 는 망가졌지만 test 파일 자체는 유효
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_passes(): assert True\n"
    )
    runner = DockerTestRunner(timeout=60)
    result = runner.run(tmp_path, language="python", test_framework="pytest")

    assert result.passed is False
    assert result.returncode == 70
    assert result.failure_reason == "[COLLECTION_ERROR]"


@docker_required
def test_normal_task_still_passes_after_collect_gate(tmp_path):
    """사전 검사가 정상 케이스를 막지 않는지 회귀 확인."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_trivial.py").write_text(
        "def test_one():\n    assert 1 == 1\n"
        "def test_two():\n    assert 2 + 2 == 4\n"
    )
    runner = DockerTestRunner(timeout=60)
    result = runner.run(tmp_path, language="python", test_framework="pytest")

    assert result.passed is True, result.stdout
    assert result.failure_reason == ""
    assert "2 passed" in result.summary


# ── 오버헤드 측정: collect-only 추가로 + 2초 이내 ────────────────────────────


@docker_required
def test_overhead_under_2_seconds_docker(tmp_path, capsys):
    """
    Docker 계층에서 3회 반복 측정. 리포트용 로깅.

    주의: 실제 "현행 대비 추가 오버헤드 < 2s" 검증은 별도
    (`test_collect_only_overhead_under_2_seconds_unit` — Docker 불필요)
    에서 본체를 담당한다. 이 테스트는 풀스택 환경에서 비정상적으로
    느려진 경우의 상한(safety net) 역할.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_trivial.py").write_text(
        "def test_one(): assert 1 == 1\n"
        "def test_two(): assert 2 == 2\n"
    )
    runner = DockerTestRunner(timeout=120)

    # 웜업: 이미지 풀/빌드 캐시 효과를 측정 구간에서 배제
    warmup = runner.run(tmp_path, language="python", test_framework="pytest")
    assert warmup.passed is True, warmup.stdout

    elapsed = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = runner.run(tmp_path, language="python", test_framework="pytest")
        elapsed.append(time.perf_counter() - t0)
        assert r.passed is True, r.stdout

    avg = sum(elapsed) / len(elapsed)
    print(f"\n[overhead:docker] runs={[f'{t:.3f}s' for t in elapsed]} avg={avg:.3f}s")
    # 풀스택(Docker 컨테이너 스핀업 포함) 상한. 진짜 오버헤드 검증은
    # unit 테스트에서 한다.
    assert avg < 30.0, (
        f"Docker E2E 평균 {avg:.2f}s — 비정상적 저속. 상세: {elapsed}"
    )
