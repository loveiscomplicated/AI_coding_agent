"""
테스트 공통 설정 및 fixture

orchestrator.dependency 모듈 테스트를 위한 공통 fixture를 정의한다.
"""

import pytest


@pytest.fixture
def simple_tasks():
    """
    의존성이 없는 단순한 태스크 리스트
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": []},
        {"id": "task-003", "depends_on": []},
    ]


@pytest.fixture
def linear_dependency_tasks():
    """
    선형 의존성 체인: task-001 → task-002 → task-003
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": ["task-001"]},
        {"id": "task-003", "depends_on": ["task-002"]},
    ]


@pytest.fixture
def diamond_dependency_tasks():
    """
    다이아몬드 의존성:
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
    자기 자신에 대한 의존성
    """
    return [
        {"id": "task-001", "depends_on": ["task-001"]},
    ]


@pytest.fixture
def invalid_dependency_tasks():
    """
    존재하지 않는 태스크를 참조하는 의존성
    """
    return [
        {"id": "task-001", "depends_on": []},
        {"id": "task-002", "depends_on": ["task-999"]},
    ]
