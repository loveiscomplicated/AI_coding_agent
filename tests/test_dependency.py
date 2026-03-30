"""
태스크 의존성 계산기 테스트

orchestrator.dependency 모듈의 resolve_order, has_cycle, validate_dependencies 함수를 검증한다.
"""

import pytest
from orchestrator.dependency import resolve_order, has_cycle, validate_dependencies


class TestResolveOrder:
    """resolve_order() 함수 테스트"""

    def test_all_tasks_with_empty_depends_on_in_single_group(self):
        """
        수락 기준 1: depends_on이 모두 빈 리스트인 태스크들은 하나의 그룹으로 반환된다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": []},
            {"id": "task-003", "depends_on": []},
        ]
        result = resolve_order(tasks)
        
        assert len(result) == 1
        assert set(result[0]) == {"task-001", "task-002", "task-003"}

    def test_dependent_task_comes_after_dependency(self):
        """
        수락 기준 2: task-002가 task-001에 의존하면 task-001 그룹이 먼저 반환된다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
        ]
        result = resolve_order(tasks)
        
        assert len(result) == 2
        assert result[0] == ["task-001"]
        assert result[1] == ["task-002"]

    def test_independent_tasks_in_same_group(self):
        """
        수락 기준 3: 의존성이 없는 task-001, task-003, task-005는 같은 그룹에 포함된다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": []},
            {"id": "task-004", "depends_on": ["task-001"]},
            {"id": "task-005", "depends_on": []},
        ]
        result = resolve_order(tasks)
        
        # 첫 번째 그룹은 의존성이 없는 task들
        assert len(result) >= 2
        assert set(result[0]) == {"task-001", "task-003", "task-005"}

    def test_multiple_dependents_same_group_after_dependency(self):
        """
        수락 기준 4: task-002(→task-001)와 task-004(→task-001)는 task-001 이후 같은 그룹에 포함된다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-004", "depends_on": ["task-001"]},
        ]
        result = resolve_order(tasks)
        
        assert len(result) == 2
        assert result[0] == ["task-001"]
        assert set(result[1]) == {"task-002", "task-004"}

    def test_chain_dependencies(self):
        """
        의존성 체인: task-001 → task-002 → task-003
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-002"]},
        ]
        result = resolve_order(tasks)
        
        assert len(result) == 3
        assert result[0] == ["task-001"]
        assert result[1] == ["task-002"]
        assert result[2] == ["task-003"]

    def test_diamond_dependency(self):
        """
        다이아몬드 의존성:
            task-001
           /        \
        task-002  task-003
           \        /
            task-004
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-001"]},
            {"id": "task-004", "depends_on": ["task-002", "task-003"]},
        ]
        result = resolve_order(tasks)
        
        assert len(result) == 3
        assert result[0] == ["task-001"]
        assert set(result[1]) == {"task-002", "task-003"}
        assert result[2] == ["task-004"]

    def test_empty_task_list(self):
        """
        빈 태스크 리스트는 빈 결과를 반환한다
        """
        tasks = []
        result = resolve_order(tasks)
        
        assert result == []

    def test_single_task_no_dependency(self):
        """
        의존성이 없는 단일 태스크
        """
        tasks = [{"id": "task-001", "depends_on": []}]
        result = resolve_order(tasks)
        
        assert len(result) == 1
        assert result[0] == ["task-001"]

    def test_complex_dependency_graph(self):
        """
        복잡한 의존성 그래프:
        task-001, task-002 (의존성 없음)
        task-003 → task-001
        task-004 → task-002
        task-005 → task-003, task-004
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": []},
            {"id": "task-003", "depends_on": ["task-001"]},
            {"id": "task-004", "depends_on": ["task-002"]},
            {"id": "task-005", "depends_on": ["task-003", "task-004"]},
        ]
        result = resolve_order(tasks)
        
        # 첫 번째 그룹: task-001, task-002
        assert set(result[0]) == {"task-001", "task-002"}
        # 두 번째 그룹: task-003, task-004
        assert set(result[1]) == {"task-003", "task-004"}
        # 세 번째 그룹: task-005
        assert result[2] == ["task-005"]


class TestHasCycle:
    """has_cycle() 함수 테스트"""

    def test_no_cycle_empty_dependencies(self):
        """
        수락 기준 5: has_cycle()은 순환 의존성이 없으면 False를 반환한다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": []},
        ]
        result = has_cycle(tasks)
        
        assert result is False

    def test_no_cycle_linear_chain(self):
        """
        선형 의존성 체인에는 순환이 없다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-002"]},
        ]
        result = has_cycle(tasks)
        
        assert result is False

    def test_no_cycle_diamond(self):
        """
        다이아몬드 의존성에는 순환이 없다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-001"]},
            {"id": "task-004", "depends_on": ["task-002", "task-003"]},
        ]
        result = has_cycle(tasks)
        
        assert result is False

    def test_cycle_self_dependency(self):
        """
        자기 자신에 대한 의존성은 순환이다
        """
        tasks = [
            {"id": "task-001", "depends_on": ["task-001"]},
        ]
        result = has_cycle(tasks)
        
        assert result is True

    def test_cycle_two_tasks(self):
        """
        수락 기준 6: has_cycle()은 A→B→A 같은 순환 의존성에 대해 True를 반환한다
        """
        tasks = [
            {"id": "task-001", "depends_on": ["task-002"]},
            {"id": "task-002", "depends_on": ["task-001"]},
        ]
        result = has_cycle(tasks)
        
        assert result is True

    def test_cycle_three_tasks(self):
        """
        A→B→C→A 순환 의존성
        """
        tasks = [
            {"id": "task-001", "depends_on": ["task-003"]},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-002"]},
        ]
        result = has_cycle(tasks)
        
        assert result is True

    def test_cycle_in_subset(self):
        """
        일부 태스크에만 순환이 있는 경우
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-004"]},
            {"id": "task-004", "depends_on": ["task-003"]},
        ]
        result = has_cycle(tasks)
        
        assert result is True

    def test_no_cycle_empty_list(self):
        """
        빈 태스크 리스트에는 순환이 없다
        """
        tasks = []
        result = has_cycle(tasks)
        
        assert result is False


class TestValidateDependencies:
    """validate_dependencies() 함수 테스트"""

    def test_valid_dependencies_empty(self):
        """
        수락 기준 8: validate_dependencies()는 의존성이 올바르면 빈 리스트를 반환한다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": []},
        ]
        result = validate_dependencies(tasks)
        
        assert result == []

    def test_valid_dependencies_with_references(self):
        """
        모든 의존성이 존재하는 태스크를 참조하는 경우
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-001", "task-002"]},
        ]
        result = validate_dependencies(tasks)
        
        assert result == []

    def test_invalid_single_missing_dependency(self):
        """
        수락 기준 7: validate_dependencies()는 존재하지 않는 ID를 참조하면 오류 메시지를 반환한다
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-999"]},
        ]
        result = validate_dependencies(tasks)
        
        assert len(result) > 0
        assert any("task-999" in msg for msg in result)
        assert any("task-002" in msg for msg in result)

    def test_invalid_multiple_missing_dependencies(self):
        """
        여러 개의 존재하지 않는 의존성
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-999", "task-888"]},
        ]
        result = validate_dependencies(tasks)
        
        assert len(result) > 0
        # 최소한 하나의 오류 메시지가 있어야 함
        assert any("task-999" in msg or "task-888" in msg for msg in result)

    def test_invalid_multiple_tasks_with_missing_dependencies(self):
        """
        여러 태스크가 존재하지 않는 의존성을 참조하는 경우
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-999"]},
            {"id": "task-003", "depends_on": ["task-888"]},
        ]
        result = validate_dependencies(tasks)
        
        assert len(result) > 0
        # 두 개의 오류가 있어야 함
        assert len(result) >= 2

    def test_valid_empty_task_list(self):
        """
        빈 태스크 리스트는 유효하다
        """
        tasks = []
        result = validate_dependencies(tasks)
        
        assert result == []

    def test_invalid_self_reference_not_in_list(self):
        """
        자신을 참조하는 경우 (자신이 존재하지 않는 것으로 간주할 수 있음)
        """
        tasks = [
            {"id": "task-001", "depends_on": ["task-001"]},
        ]
        result = validate_dependencies(tasks)
        
        # 자신을 참조하는 것은 유효한 의존성이므로 오류가 없어야 함
        # (순환은 has_cycle에서 검사)
        assert result == []

    def test_mixed_valid_and_invalid_dependencies(self):
        """
        일부는 유효하고 일부는 유효하지 않은 의존성
        """
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-001", "task-999"]},
        ]
        result = validate_dependencies(tasks)
        
        assert len(result) > 0
        assert any("task-999" in msg for msg in result)
