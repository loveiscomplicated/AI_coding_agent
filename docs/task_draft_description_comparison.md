# 태스크 초안 description 품질 비교 — `_DRAFT_SYSTEM_PROMPT` 강화

## 문서의 성격 (중요)

이 문서는 **실 LLM 호출 결과의 전·후 비교가 아니다.** 본 작업 범위에서
`POST /api/tasks/draft`를 실제로 실행하지 않았다 — 외부 LLM 호출이 필요하고,
결과 비결정성(temperature)으로 단일 샘플이 의미 있는 지표가 되지 못하기 때문이다.

대신 이 문서는:

- **변경 전**: 과거 `data/tasks.yaml`에 이미 저장되어 있는 `task-001` description 원문
  (이전 프롬프트 기반으로 실제 생성된 산출물).
- **변경 후**: 새 프롬프트의 4섹션 구조를 **같은 저장소의 task-001~task-004가 현재
  담고 있는 정보만**으로 재구성한 예시. 본문 근거를 각 문장 옆에 출처 표시로 달았다.
  원문에 없는 사실(예: 직렬화 정책, 고도/방위각 필드, 패키지 배치 근거)은 의도적으로
  제거했다 — 그런 문장은 새 프롬프트의 "비고려 항목 근거 제약" 조항을 위반한다.

즉 이 문서는 "프롬프트가 뭘 유도하려는가"의 설계 의도서이지, "뭐가 실제로
좋아졌는가"의 실측 보고서가 아니다. 실측은 사용자가 실제 파이프라인을 돌려
`data/tasks.yaml`을 교체해 보아야 가능하다.

---

## 변경 전 description (`data/tasks.yaml:4-5`의 실제 출력)

> 앱 전체에서 공통으로 사용하는 핵심 도메인 모델을 정의한다. Coordinate(위도/경도),
> Place(장소명, 좌표, POI ID), Route(출발지, 목적지, 경로 좌표 리스트, 총 거리)를
> data class로 구현한다.

**길이**: 약 90자

### 문제점

| 항목 | 평가 |
| --- | --- |
| "무엇을" | ○ 모델 3개를 만든다는 건 명확 |
| "왜" | ✕ 왜 필요한지, 프로젝트 어디에서 쓰이는지 없음 |
| 기술 제약 | △ 필드 목록은 있지만 검증 규칙·예외 조건은 acceptance_criteria에만 존재 |
| 후속/선행 맥락 | ✕ 이 모델을 누가 쓰는지 description 내부에 전혀 없음 |
| 비고려 항목 | ✕ 스코프 경계 부재 |

TestWriter가 보면 acceptance_criteria 4개를 기계적으로 테스트할 수는 있지만,
"왜 이 4개인가"·"다른 추가 검증이 범위에 포함되는가"는 유추해야 한다.

---

## 변경 후 description (출처 표시 포함 재구성)

아래는 **`data/tasks.yaml`의 task-001~task-004 원문만**으로 구성한 예시다.
각 문장 끝 `[소스: ...]` 태그는 그 정보가 저장소의 어느 줄에서 나왔는지를 가리킨다.
표기 없는 문장은 `task-001`의 `target_files`·`acceptance_criteria`에서 자명하게
도출되는 것으로 한정했다.

> ### 목적과 배경
> 이 세 타입은 후속 태스크가 인자·반환으로 직접 사용한다. `MapService` 인터페이스
> 정의가 `searchPlace(...): Result<List<Place>>`, `getWalkingRoute(...): Result<Route>`,
> `distanceToRoute(current: Coordinate, route: Route): Double`로 세 타입을 모두 쓰며
> `[소스: task-003 설명 — data/tasks.yaml:47-50]`, `RouteDeviationDetector`가 현재
> GPS 좌표와 `Route.waypoints` 간 거리를 계산한다
> `[소스: task-004 설명 — data/tasks.yaml:71-73]`.
>
> ### 기술 요구사항
> - `Coordinate`: `latitude(Double)`, `longitude(Double)` 필드.
>   유효 범위(-90~90, -180~180)를 벗어나면 `IllegalArgumentException`.
>   `[소스: task-001 acceptance_criteria — data/tasks.yaml:7-8]`
> - `Place`: `name(String)`, `coordinate(Coordinate)`, `poiId(String)` 필드.
>   `[소스: task-001 acceptance_criteria — data/tasks.yaml:9]`
> - `Route`: `origin(Place)`, `destination(Place)`, `waypoints(List<Coordinate>)`,
>   `totalDistanceMeters(Double)` 필드. `waypoints`가 빈 리스트면 예외.
>   `[소스: task-001 acceptance_criteria — data/tasks.yaml:10-11]`
> - 세 타입 모두 `data class`로 정의하여 `equals`·`hashCode`·`copy` 기본 구현을
>   활용한다. `[소스: task-001 acceptance_criteria — data/tasks.yaml:12]`
>
> ### 인접 컨텍스트
> - 선행 태스크: 없음. `[소스: task-001 depends_on=[] — data/tasks.yaml:18]`
> - 후속 태스크:
>   - `task-002` — 이 세 모델에 대한 단위 테스트.
>     `[소스: task-002 depends_on=[task-001] — data/tasks.yaml:38-39]`
>   - `task-003` — `MapService` 인터페이스가 세 타입을 입출력으로 소비.
>     `[소스: task-003 depends_on=[task-001] — data/tasks.yaml:62-63]`
>   - `task-004` — `RouteDeviationDetector`가 `Route`·`Coordinate`를 소비.
>     `[소스: task-004 설명 — data/tasks.yaml:71-73; depends_on=[task-001] — data/tasks.yaml:85-86]`
>
> ### 비고려 항목
> - 단위 테스트는 이 태스크의 범위가 아니며 `task-002`가 전담한다.
>   `[소스: (b) 근거 — task-002가 동일 타입의 테스트를 담당]`
> - `MapService` 인터페이스 정의, 어댑터 구현체(예: `task-007` KakaoMapService)는
>   `task-003` 이후 태스크에서 다룬다.
>   `[소스: (b) 근거 — task-003이 인터페이스 담당 / task-007 KakaoMapService 구현, data/tasks.yaml:138-153]`
> - 그 외 명시적 비범위 없음. (context_doc에 추가 연기 결정이 있다면 실 생성 시
>   LLM이 여기에 덧붙일 것.)

**길이**: 약 780자 (출처 태그 포함).

### 이 구성에서 의도적으로 **제거한** 사실

아래는 이전 버전(리뷰 이전) 예시에 포함되었지만 출처 확인이 불가능하여 삭제한
항목들이다. 현재 프롬프트(수정 후)는 이런 문장을 `비고려 항목 근거 제약`
조항으로 금지한다.

- "직렬화 어노테이션(@Serializable 등)은 붙이지 않는다" — `data/tasks.yaml`에
  근거 없음. context_doc 원문 없이 재구성 불가.
- "고도·방위각 필드는 현재 스프린트 범위 밖" — 스프린트 경계 정보 없음.
- "어댑터 경계를 도메인 모델에서 긋는다는 결정" — 채택 문장의 출처를 저장소
  내부에서 확인할 수 없음.
- 패키지 경로(`domain/model/`)의 설계 근거 — target_files 경로 외 추가 주장 없음.

---

## 변경 후 프롬프트의 장치 요약

| 프롬프트 장치 | 효과 | 실패 시 감지 |
| --- | --- | --- |
| 4섹션 구조 강제 | TestWriter/Implementer/Reviewer가 "왜/제약/비범위"를 균일하게 획득 | `_find_missing_sections()` 경고 |
| 길이 400~800자 가이드 | 지나친 과부족을 방지 | `len(description) < 100` 경고 |
| 비고려 항목 근거 제약 (수정 후 추가) | 환각성 제외사항으로 Reviewer가 오판하는 것을 방지 | 자동 감지는 아직 없음 (리뷰 권장 사항에 대응 예정) |

---

## 보정 로직 보강

`_sanitize_task_draft()`는 경고만 추가하고 태스크는 저장을 막지 않는다.
경고 항목 두 가지:

1. **description 100자 미만 경고**
   너무 짧은 경우 "목적과 배경, 기술 요구사항, 인접 컨텍스트, 비고려 항목이
   포함되어야 합니다"라는 메시지를 warnings에 추가.

2. **섹션 누락 경고 (헤더 기반, 한·영 별칭 지원)**
   Markdown 헤더(`#`으로 시작하는 줄)에서만 섹션을 인정한다. 본문에만 키워드가
   등장하면 섹션으로 인정하지 않는다. 각 섹션은 다음 별칭을 허용한다:

   | 섹션 | 별칭 |
   | --- | --- |
   | 목적과 배경 | `목적`, `배경`, `Purpose and Background`, `Purpose`, `Background` |
   | 기술 요구사항 | `기술 요구`, `요구사항`, `Technical Requirements`, `Requirements`, `Specification` |
   | 인접 컨텍스트 | `인접`, `컨텍스트`, `Adjacent Context`, `Related Context`, `Context` |
   | 비고려 항목 | `비고려`, `비범위`, `Out of Scope`, `Out-of-Scope`, `Not in Scope`, `Non-Goals` |

   누락 시 한국어 라벨로 보고 (영어 헤더를 썼어도 보고는 한국어 — 리뷰 가독성 우선).

## 검증

`tests/test_task_draft_sanitize.py` — 17 케이스:

- target_files 정규화 회귀 방지 (4)
- description 길이 경고 (2)
- 섹션 누락/부분 누락/전체 포함 한국어 (3)
- 영어 헤더 / 한·영 혼용 / Non-Goals 별칭 (3)
- 헤더 없이 본문 키워드만 있는 경우 (1)
- 대체 헤더 레벨(`##`, `####`, 선행 공백) 및 트레일링 `#` (1)
- 영어 헤더 사용 시 한국어 라벨로 누락 보고 (1)
- 경고가 태스크를 무효화하지 않음 (2)

전체 스위트(1079 passed, 2 skipped) 통과 확인.

## 남은 과제 (리뷰 권장 사항)

1. **비고려 항목 환각 자동 감지**: 현재는 프롬프트 레벨 제약에만 의존한다.
   LLM이 이 제약을 어기고 근거 없는 제외사항을 쓰는지 자동으로 탐지하려면
   context_doc/인접 태스크와의 대조가 필요하다. 경험상 샘플을 먼저 수집하고
   오탐·미탐을 측정한 뒤에 구현하는 편이 낫다.

2. **짧은 태스크에 대한 완충 규칙**: 현재 400~800자 가이드가 모든 태스크에
   일괄 적용된다. 정말 자명한 태스크까지 형식적 채우기를 유도할 위험이 있다.
   `_find_missing_sections`가 누락을 "경고"로만 보고하는 현재 설계는 이 완충을
   일부 흡수하지만, 프롬프트 자체에 "정말 자명한 경우 최소 250자 허용"을
   명기할지는 실제 샘플을 본 뒤에 결정할 문제다.

3. **실 생성 결과 스냅샷 테스트**: `/api/tasks/draft`를 오프라인 fixture(고정
   context_doc, 고정 LLM 응답 mock)로 재현하여 섹션 충족률을 회귀 측정하는
   테스트. 변경 효과를 방어하는 데 유용하나 본 작업 범위 외.
