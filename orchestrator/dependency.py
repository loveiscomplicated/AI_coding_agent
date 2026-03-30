"""
태스크 의존성 계산기

태스크 간 depends_on 관계를 분석하여 실행 순서를 계산한다.
Kahn's algorithm으로 위상 정렬을 수행한다.
"""


def resolve_order(tasks: list[dict]) -> list[list[str]]:
    """
    태스크의 의존성을 분석하여 병렬 실행 가능한 그룹의 리스트를 반환한다.
    
    각 task는 {"id": str, "depends_on": list[str]} 구조를 가진다.
    반환값은 병렬 실행 가능한 그룹의 리스트이다.
    
    예:
        [["task-001", "task-003", "task-005"], ["task-002", "task-004"]]
        의미: 첫 번째 그룹을 모두 완료한 후 두 번째 그룹을 실행한다.
    
    Args:
        tasks: 태스크 리스트. 각 태스크는 id와 depends_on을 포함한다.
    
    Returns:
        병렬 실행 가능한 그룹의 리스트. 각 그룹은 태스크 ID의 리스트이다.
    """
    if not tasks:
        return []
    
    # 태스크 ID 집합 생성
    task_ids = {task["id"] for task in tasks}
    
    # 각 태스크의 in-degree 계산 (의존성 개수)
    in_degree = {task["id"]: len(task["depends_on"]) for task in tasks}
    
    # 역 그래프 생성: 각 태스크가 어떤 태스크들에 의해 의존되는지
    dependents = {task["id"]: [] for task in tasks}
    for task in tasks:
        for dep in task["depends_on"]:
            if dep in task_ids:  # 존재하는 의존성만 처리
                dependents[dep].append(task["id"])
    
    # Kahn's algorithm 수행
    result = []
    processed = set()
    
    while len(processed) < len(task_ids):
        # in-degree가 0인 모든 태스크 찾기 (아직 처리되지 않은 것만)
        current_level = [
            task_id for task_id in task_ids 
            if in_degree[task_id] == 0 and task_id not in processed
        ]
        
        if not current_level:
            break
        
        # 현재 레벨의 태스크들을 결과에 추가
        result.append(sorted(current_level))
        
        # 현재 레벨의 태스크들을 처리 완료로 표시
        for task_id in current_level:
            processed.add(task_id)
            
            # 이 태스크에 의존하는 모든 태스크의 in-degree 감소
            for dependent in dependents[task_id]:
                in_degree[dependent] -= 1
    
    return result


def has_cycle(tasks: list[dict]) -> bool:
    """
    의존성 그래프에 순환이 있는지 확인한다.
    
    Args:
        tasks: 태스크 리스트. 각 태스크는 id와 depends_on을 포함한다.
    
    Returns:
        순환이 있으면 True, 없으면 False를 반환한다.
    """
    if not tasks:
        return False
    
    # 태스크 ID 집합 생성
    task_ids = {task["id"] for task in tasks}
    
    # 각 태스크의 in-degree 계산
    in_degree = {task["id"]: len(task["depends_on"]) for task in tasks}
    
    # 역 그래프 생성
    dependents = {task["id"]: [] for task in tasks}
    for task in tasks:
        for dep in task["depends_on"]:
            if dep in dependents:
                dependents[dep].append(task["id"])
    
    # Kahn's algorithm으로 위상 정렬 시도
    processed_count = 0
    
    while True:
        # in-degree가 0인 모든 태스크 찾기
        current_level = [task_id for task_id in task_ids if in_degree[task_id] == 0]
        
        if not current_level:
            break
        
        # 현재 레벨의 태스크들을 처리
        for task_id in current_level:
            in_degree[task_id] = -1  # 처리 완료 표시
            processed_count += 1
            
            # 이 태스크에 의존하는 모든 태스크의 in-degree 감소
            for dependent in dependents[task_id]:
                in_degree[dependent] -= 1
    
    # 모든 태스크가 처리되지 않았으면 순환이 있음
    return processed_count != len(tasks)


def validate_dependencies(tasks: list[dict]) -> list[str]:
    """
    존재하지 않는 태스크 ID를 depends_on에 참조하는 경우 오류 메시지 리스트를 반환한다.
    
    Args:
        tasks: 태스크 리스트. 각 태스크는 id와 depends_on을 포함한다.
    
    Returns:
        오류 메시지 리스트. 문제 없으면 빈 리스트를 반환한다.
    """
    if not tasks:
        return []
    
    # 존재하는 태스크 ID 집합
    task_ids = {task["id"] for task in tasks}
    
    errors = []
    
    # 각 태스크의 의존성 검증
    for task in tasks:
        for dep in task["depends_on"]:
            if dep not in task_ids:
                errors.append(f"Task '{task['id']}' depends on non-existent task '{dep}'")
    
    return errors
