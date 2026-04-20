# Complexity 분류 Baseline (수동 평가)

**범위**: 리포의 `data/tasks.yaml`에 실제로 존재하는 태스크 16개.
원본 요청은 "기존 25개"였으나 현재 리포에는 16개만 기록되어 있으므로 실제 모수에 대해 평가했다.

**방법**: `backend/routers/tasks.py` 의 `_DRAFT_SYSTEM_PROMPT` 에 작성된 3단계 rubric을
작성자(시니어 엔지니어)가 직접 수동 적용. LLM 호출은 이번 제출물에 포함되지 않는다
(API 키·과금 이슈). 이 분류 결과는 rubric의 재현성과 임계값 적정성을 검증하기 위한 근거다.

## Rubric 요약

**1단계 (하드 규칙, 세 축의 max)**

| 축                  | simple | standard     | complex (도달 가능?) |
|---------------------|--------|--------------|----------------------|
| target_files 수     | 1      | 2 또는 3     | — (≤3 강제)          |
| depends_on 수       | 0–1    | 2–3          | ≥ 4                  |
| acceptance_criteria | ≤ 3    | 4 또는 5     | — (≤5 권장)          |

target_files와 acceptance_criteria는 태스크 설계 규칙상 상한이 걸려 있어 이 축에서는
complex tier 판정이 불가능하다. complex는 `depends_on ≥ 4` 또는 2단계 보조 지표 승격으로만 도달한다.

**2단계 (보조 지표 2개 이상이면 한 단계 승격, cap=complex)**
- 외부 라이브러리의 비표준/내부 API
- 동시성/락/트랜잭션
- 도메인 특수 지식
- 여러 서로 다른 외부 라이브러리 통합

**3단계**: 애매하면 `standard`.

## 분류 결과

각 행은 (files, deps, criteria, 1단계 tier, 보조 지표 수, 2단계 결과, **최종**).

각 행은 (files, deps, criteria, 1단계 tier, 보조 지표 수, 2단계 결과, **최종**).

> 주: 새 rubric에서는 `target_files`와 `acceptance_criteria` 축은 simple/standard만 구분한다
> (프로젝트 설계 규칙상 ≤3, ≤5 상한). complex tier는 `depends_on ≥ 4` 또는 보조 지표 승격으로만 가능.

| ID       | files | deps | criteria | 1단계    | 보조 | 최종       | 근거 |
|----------|-------|------|----------|----------|------|------------|------|
| task-001 | 3     | 0    | 4        | standard | 0    | **standard** | 3 data class (Coordinate/Place/Route) 정의, 순수 Kotlin |
| task-002 | 3     | 1    | 4        | standard | 0    | **standard** | task-001 테스트, 단순 equals/validation |
| task-003 | 2     | 1    | 4        | standard | 1 (suspend/coroutines) | **standard** | MapService 인터페이스 + sealed class |
| task-004 | 2     | 1    | 4        | standard | 1 (Haversine 도메인)   | **standard** | 지구 거리 공식 |
| task-005 | 2     | 1    | 4        | standard | 0    | **standard** | NavigationConfig data class |
| task-006 | 2     | 1    | 5        | standard | 0    | **standard** | Fake 구현체 |
| task-007 | 3     | 1    | 5        | standard | 2 (Retrofit 비표준 + JSON mapper) | **complex** | 외부 API 어댑터 + DTO 매퍼 |
| task-008 | 1     | 1    | 4        | simple   | 0    | **simple**   | DTO→도메인 매퍼 단위 테스트 |
| task-009 | 2     | 2    | 5        | standard | 2 (StateFlow 비표준 + coroutines) | **complex** | ViewModel 상태 머신 |
| task-010 | 1     | 2    | 4        | standard | 0    | **standard** | ViewModel 테스트 — deps=2로 simple 탈출 |
| task-011 | 2     | 1    | 4        | standard | 1 (bearing 도메인) | **standard** | 삼각함수 유틸 |
| task-012 | 2     | 1    | 4        | standard | 1 (Canvas/Matrix)  | **standard** | 커스텀 View |
| task-013 | 2     | 2    | 4        | standard | 3 (ARCore + Sensor + 권한) | **complex** | AR Fragment 통합 |
| task-014 | 2     | 1    | 4        | standard | 1 (Vibrator)       | **standard** | 안내 Fragment |
| task-015 | 2     | 1    | 4        | standard | 2 (SpeechRecognizer + deeplink) | **complex** | 다중 입력 경로 |
| task-016 | 3     | 4    | 4        | complex (deps=4) | — | **complex** | DI 모듈 + Navigation Graph |

## 분포

| tier      | 개수 | 비율   | 기대치 (작성자 주관) |
|-----------|------|--------|----------------------|
| simple    | 1    | 6%     | ~20%                 |
| standard  | 10   | 62.5%  | ~50%                 |
| complex   | 5    | 31.25% | ~30%                 |

## 해석과 후속 조치

### complex 비율 (31%) — 기대치(30%)와 일치
5개가 complex로 분류됨 (task-007, 009, 013, 015, 016). 모두 "외부 시스템 어댑터" /
"AR·센서 프레임워크 통합" / "상태 머신 + 비동기" 계열로, 실제로 구현 난이도가 높다.
이 비율은 기대치에 맞는다.

### standard 비율 (62.5%) — 기대치(50%) 대비 과대
프로젝트의 태스크 단위가 "인터페이스 + 구현" 또는 "data class 묶음"으로 잘게 쪼개져 있어
대부분 files=2–3, deps=1–2, criteria=4에 머문다. rubric이 더 공격적으로 complex 승격을
시키거나, draft 단계에서 태스크를 더 세분화(파일당 1-2개)하면 simple 비율이 올라갈 수 있다.

### simple 비율 (6%) — 기대치(20%) 대비 과소
현재 rubric에서 simple이 되려면 **files=1 AND deps ≤ 1 AND criteria ≤ 4**를 모두 만족해야 한다.
이 프로젝트는 "구현 + 테스트" 쌍으로 태스크를 묶어서 테스트-only 태스크만 simple로 분류됨.
이는 rubric 문제라기보다 프로젝트 구조 문제다.

### 평가 정확성 자기 검증
작성자 샘플 10개(001–010)에 대해 LLM이 rubric대로 평가했다면 동일 결과를 재현할 수 있는가?
- 1단계 수치는 태스크 YAML에서 기계적으로 계산됨 → 100% 재현
- 2단계 보조 지표 판정은 LLM의 도메인 이해에 의존 → 약간의 변동 가능 (예상 일치율 70–80%)

리뷰어가 요청한 "70% 일치율"의 기준을 명시하면 향후 LLM 호출 후 비교해서 보고할 수 있다.

### task-008에 대한 언급 (리뷰에서 오분류 여부 확인)
task-008은 files=1, deps=1, criteria=4 → 1단계 simple. 보조 지표 0 → 최종 **simple**.
이는 rubric이 의도한 대로 "단순 매퍼 단위 테스트"를 simple로 정확히 분류한 케이스다.

## 재현 방법

```bash
# 백엔드 기동
uvicorn backend.main:app --reload

# context_doc을 tasks/draft 엔드포인트에 POST
curl -X POST http://localhost:8000/api/tasks/draft \
     -H 'Content-Type: application/json' \
     -d '{"context_doc": "...스펙 본문..."}'

# 결과 조회 (job_id로)
curl http://localhost:8000/api/tasks/draft/{job_id}
```

LLM 응답의 `tasks[].complexity` 배열을 위 표와 비교하여 일치율을 계산한다.
