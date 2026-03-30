"""
테스트 공통 설정 및 fixture

orchestrator.dependency 모듈 테스트를 위한 공통 fixture를 정의한다.
"""

import pytest


@pytest.fixture
def simple_linear_tasks():
    """
    선형 의존성 태스크: task-001 → task-002 → task-003
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": ["task-001"]},
        {"id": "task-003", "depends_on": ["task-002"]},
    ]


@pytest.fixture
def diamond_dependency_tasks():
    """
    다이아몬드 의존성 태스크:
        task-001
       /        \
    task-002  task-003
       \        /
        task-004
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": ["task-001"]},
        {"id": "task-003", "depends_on": ["task-001"]},
        {"id": "task-004", "depends_on": ["task-002", "task-003"]},
    ]


@pytest.fixture
def independent_tasks():
    """
    의존성이 없는 독립적인 태스크들
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": []},
        {"id": "task-003", "depends_on": []},
    ]


@pytest.fixture
def cyclic_two_tasks():
    """
    두 개 태스크의 순환 의존성: task-001 ↔ task-002
    """
    return [
        {"id": "task-001", "depends_on": ["task-002"]},
        {"id": "task-002", "depends_on": ["task-001"]},
    ]


@pytest.fixture
def cyclic_three_tasks():
    """
    세 개 태스크의 순환 의존성: task-001 → task-002 → task-003 → task-001
    """
    return [
        {"id": "task-001", "depends_on": ["task-003"]},
        {"id": "task-002", "depends_on": ["task-001"]},
        {"id": "task-003", "depends_on": ["task-002"]},
    ]


@pytest.fixture
def self_dependent_task():
    """
    자기 자신에 의존하는 태스크
    """
    return [
        {"id": "task-001", "depends_on": ["task-001"]},
    ]


@pytest.fixture
def tasks_with_missing_dependency():
    """
    존재하지 않는 태스크를 참조하는 의존성
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": ["task-999"]},
    ]


@pytest.fixture
def complex_parallel_tasks():
    """
    복잡한 병렬 실행 그룹:
    - 그룹 1: task-001, task-002 (의존성 없음)
    - 그룹 2: task-003, task-004 (task-001, task-002에 의존)
    - 그룹 3: task-005 (task-003, task-004에 의존)
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": []},
        {"id": "task-003", "depends_on": ["task-001"]},
        {"id": "task-004", "depends_on": ["task-002"]},
        {"id": "task-005", "depends_on": ["task-003", "task-004"]},
    ]
