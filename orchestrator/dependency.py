"""
태스크 의존성 계산기

Kahn's algorithm을 사용하여 태스크 간 의존성을 분석하고 실행 순서를 계산한다.
"""

from collections import deque


def resolve_order(tasks: list[dict]) -> list[list[str]]:
    """
    태스크 목록을 받아 병렬 실행 가능한 그룹의 리스트를 반환한다.

    Kahn's algorithm(위상 정렬)을 사용하여 의존성 순서를 계산한다.
    같은 그룹 내 태스크들은 동시에 실행 가능하다.

    Args:
        tasks: {"id": str, "depends_on": list[str]} 구조의 태스크 딕셔너리 리스트

    Returns:
        병렬 실행 가능한 그룹의 리스트.
        예: [["task-001", "task-003"], ["task-002", "task-004"]]
    """
    if not tasks:
        return []

    # 진입 차수(in-degree) 계산 및 인접 리스트 구성
    in_degree = {task["id"]: 0 for task in tasks}
    # 각 태스크가 완료되면 해제되는 의존 태스크 목록 (역방향 그래프)
    dependents: dict[str, list[str]] = {task["id"]: [] for task in tasks}

    for task in tasks:
        task_id = task["id"]
        for dep in task["depends_on"]:
            # dep가 tasks에 존재하는 경우에만 처리
            if dep in in_degree:
                in_degree[task_id] += 1
                dependents[dep].append(task_id)

    # 진입 차수가 0인 태스크들을 초기 큐에 추가
    queue = deque(
        sorted(task_id for task_id, degree in in_degree.items() if degree == 0)
    )

    result = []

    while queue:
        # 현재 레벨(그룹)의 태스크들을 모두 처리
        current_group = sorted(queue)
        result.append(current_group)
        queue.clear()

        next_group_candidates = []
        for task_id in current_group:
            for dependent in dependents[task_id]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_group_candidates.append(dependent)

        # 다음 그룹 후보를 큐에 추가 (정렬하여 일관성 유지)
        for candidate in sorted(next_group_candidates):
            queue.append(candidate)

    return result


def has_cycle(tasks: list[dict]) -> bool:
    """
    태스크 의존성 그래프에 순환이 있는지 확인한다.

    Kahn's algorithm을 사용하여 위상 정렬 후 처리되지 않은 노드가 있으면 순환이 존재한다.

    Args:
        tasks: {"id": str, "depends_on": list[str]} 구조의 태스크 딕셔너리 리스트

    Returns:
        순환이 있으면 True, 없으면 False
    """
    if not tasks:
        return False

    # 진입 차수 계산
    in_degree = {task["id"]: 0 for task in tasks}
    dependents: dict[str, list[str]] = {task["id"]: [] for task in tasks}

    for task in tasks:
        task_id = task["id"]
        for dep in task["depends_on"]:
            if dep in in_degree:
                in_degree[task_id] += 1
                dependents[dep].append(task_id)
            # 존재하지 않는 dep는 무시 (validate_dependencies에서 처리)

    # 진입 차수가 0인 태스크들로 시작
    queue = deque(task_id for task_id, degree in in_degree.items() if degree == 0)
    visited_count = 0

    while queue:
        task_id = queue.popleft()
        visited_count += 1

        for dependent in dependents[task_id]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # 모든 태스크가 처리되지 않았다면 순환이 존재
    return visited_count != len(tasks)


def validate_dependencies(tasks: list[dict]) -> list[str]:
    """
    태스크 의존성의 유효성을 검사한다.

    존재하지 않는 태스크 ID를 depends_on에 참조하는 경우 오류 메시지를 반환한다.

    Args:
        tasks: {"id": str, "depends_on": list[str]} 구조의 태스크 딕셔너리 리스트

    Returns:
        오류 메시지 리스트. 문제 없으면 빈 리스트.
    """
    if not tasks:
        return []

    # 존재하는 모든 태스크 ID 집합
    task_ids = {task["id"] for task in tasks}
    errors = []

    for task in tasks:
        task_id = task["id"]
        for dep in task["depends_on"]:
            if dep not in task_ids:
                errors.append(
                    f"태스크 '{task_id}'의 의존성 '{dep}'이(가) 존재하지 않습니다."
                )

    return errors
