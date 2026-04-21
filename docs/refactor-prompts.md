# Multi-Agent 시스템 리팩토링 프롬프트 모음

> 작성 → 검토 이중화 워크플로용.
> 한쪽 CLI 에이전트(예: Claude Code)로 **구현**, 다른 쪽(예: Codex)으로 **검토**.
> 권장 실행 순서: **#1 → #3 → #6 → #7 → #2 → #4 → #5**
> #1(측정 인프라) 없이 나머지 작업의 효과를 검증할 수 없으므로 반드시 #1을 먼저 완료할 것.
> #7(태스크 컨텍스트 풍부화)은 #6(의도 전달) 직후 권장 — 둘이 시너지가 큼.

---

## 공통 검토 원칙 (모든 검토 프롬프트에 암묵적으로 적용됨)

검토자 에이전트는 다음 관점에서 구현을 평가한다:

1. **회귀 위험**: 기존 테스트가 깨지지 않는가? 기존 동작을 조용히 바꾸지 않는가?
2. **계약 일치**: 작성자 프롬프트가 요구한 인터페이스/동작이 정확히 구현되었는가?
3. **에지 케이스**: null, 빈 입력, 예외 경로가 처리되는가?
4. **측정 가능성**: 구현의 효과를 이후에 검증할 수 있도록 되어 있는가?
5. **미니멀리티**: 필요 이상으로 다른 파일을 건드리지 않았는가? 스코프가 유지되는가?

검토자는 "LGTM"으로 끝내지 말 것. 반드시 아래 형식으로 회신한다:

```
## 검토 결과: APPROVED / CHANGES_REQUESTED

### 잘 된 점
- ...

### 반드시 수정 (CHANGES_REQUESTED 사유)
- 파일:줄 — 문제 설명 — 제안 수정

### 권장 사항 (non-blocking)
- ...

### 확인한 테스트 실행 결과
- pytest 실행 커맨드 + 결과
```

---

## #1. Per-call Token Logging (측정 인프라)

### 배경
- 현재 `agent-data/reports/task-*.yaml`에는 `total_tokens` 합산값만 있어 캐시 적중률을 알 수 없음.
- 나머지 모든 최적화(캐싱, 압축 등)의 효과를 검증할 수 없는 상태.
- Anthropic: `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens` 필드 제공.
- OpenAI: `usage.prompt_tokens_details.cached_tokens` 제공 (자동 캐싱).
- GLM: OpenAI 호환이지만 실제 지원 여부는 응답 구조를 보고 판단해야 함.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 다음 작업을 수행하라.

## 목적
각 LLM 호출마다 토큰 사용 내역(input, output, cached_read, cached_write)을 구조화해서
기록하는 측정 인프라를 추가한다. 이후 최적화 작업(#2~#5)의 효과 검증 기반이 된다.

## 범위 — 수정 대상 파일
1. `llm/base.py` — `LLMResponse`에 token 필드 추가
2. `llm/claude_client.py`, `llm/openai_client.py`, `llm/glm_client.py`, `llm/ollama_client.py`
   — 각 API 응답에서 토큰 정보 추출하여 `LLMResponse`에 채움
3. `core/loop.py` — ReactLoop가 호출별 토큰을 누적
4. `orchestrator/pipeline.py` — 태스크별 토큰 내역을 TaskReport로 전달
5. `metrics/collector.py` 또는 `orchestrator/report.py` — TaskReport 스키마에 필드 추가
6. 태스크 완료 시 `agent-data/logs/{task_id}_{timestamp}.jsonl` 형태로
   per-call 로그 파일도 별도 저장 (관찰용)

## 구체 요구사항

### 1) LLMResponse 확장
```python
@dataclass
class LLMResponse:
    # 기존 필드 유지
    ...
    input_tokens: int = 0
    output_tokens: int = 0
    cached_read_tokens: int = 0      # 캐시 hit (Anthropic: cache_read_input_tokens,
                                     #           OpenAI: prompt_tokens_details.cached_tokens)
    cached_write_tokens: int = 0     # 캐시 생성 (Anthropic만: cache_creation_input_tokens)
    model: str = ""                  # 실제 사용된 모델명 (로그 추적용)
```

### 2) 각 클라이언트별 추출 규칙
- **ClaudeClient**: `message.usage.input_tokens`, `output_tokens`,
  `cache_read_input_tokens` (없으면 0), `cache_creation_input_tokens` (없으면 0)
- **OpenaiClient**: `usage.prompt_tokens`, `completion_tokens`,
  `usage.prompt_tokens_details.cached_tokens` (없으면 0). cached_write은 항상 0.
- **GlmClient**: OpenAI와 동일 스키마 시도. `prompt_tokens_details`가 없으면 cached는 0으로 처리.
  응답 구조가 다를 경우 `getattr(..., None)` 방어 코드 사용.
- **OllamaClient**: 토큰 정보가 없으면 전부 0.

### 3) ReactLoop 누적
ReactLoop에 다음 필드 추가:
```python
self.call_log: list[dict] = []  # per-call 기록
self.total_input: int = 0
self.total_output: int = 0
self.total_cached_read: int = 0
self.total_cached_write: int = 0
```
매 `self.llm.chat()` 호출 직후 `response.input_tokens` 등을 누적하고,
`call_log`에 다음 dict를 append:
```python
{
    "timestamp": ISO8601,
    "iteration": n,
    "model": response.model,
    "input_tokens": ...,
    "output_tokens": ...,
    "cached_read_tokens": ...,
    "cached_write_tokens": ...,
    "tool_calls": [tool_name, ...],  # 이 호출에서 사용한 도구 이름
}
```

### 4) 파이프라인 집계
`pipeline.py`가 태스크 완료 시 각 에이전트(TestWriter/Implementer/Reviewer/Intervention)의
ReactLoop.total_* 값을 role별로 집계하여 TaskReport에 저장.

### 5) TaskReport 스키마 추가
```yaml
token_usage:
  test_writer:
    input: 12345
    output: 2345
    cached_read: 8000
    cached_write: 4000
  implementer:
    input: ...
    ...
  reviewer: ...
  intervention: ...   # 있을 때만
total_input_tokens: ...
total_output_tokens: ...
total_cached_read_tokens: ...
total_cached_write_tokens: ...
cache_hit_rate: 0.63          # cached_read / (input + cached_read)
```
기존 `total_tokens` 필드는 하위 호환을 위해 유지하되,
`input + output + cached_read` 합계로 재정의.

### 6) JSONL 로그 파일
`agent-data/logs/{task_id}_{timestamp}.jsonl` — 각 줄이 ReactLoop.call_log 항목 하나.
role 필드 추가 ("test_writer"/"implementer"/"reviewer"/"intervention"/"orchestrator" 등).

## 하지 말 것
- 기존 API 동작/시그니처 변경 금지 (`LLMResponse`의 기존 필드는 그대로)
- 기존 테스트 수정 금지 (필요하면 새 테스트만 추가)
- 캐싱 전략 자체는 이 작업에서 건드리지 않는다 (측정만)

## 테스트 요구사항
1. `tests/test_token_tracking.py` 신규 작성
   - 각 클라이언트 mock에서 토큰 필드가 올바르게 파싱되는지
   - ReactLoop가 multi-turn에서 토큰을 누적하는지
   - TaskReport YAML에 token_usage가 제대로 저장/로드되는지
2. 기존 E2E 테스트 한 개 실행하여 회귀 없음 확인

## 산출물
- 수정된 파일 diff 요약 (파일별 추가/수정 줄 수)
- `tests/test_token_tracking.py` 실행 결과 (pass 확인)
- 샘플 TaskReport YAML 1개 (더미 데이터로 생성하여 스키마 검증용)
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 "per-call token logging"
인프라를 구현했다. 구현물을 검토하라.

## 검토 체크리스트

### A. 인터페이스 호환성
- [ ] `LLMResponse`의 기존 필드가 모두 유지되는가?
- [ ] 기존 `total_tokens` 필드가 깨지지 않고 재정의되었는가?
- [ ] 토큰 필드가 없는 응답(Ollama 등)도 에러 없이 처리되는가?

### B. 각 클라이언트의 토큰 추출 정확성
- [ ] ClaudeClient: `cache_read_input_tokens`, `cache_creation_input_tokens` 둘 다
      누락 시 0으로 처리되는가? (일부 응답에선 필드 자체가 없음)
- [ ] OpenaiClient: `prompt_tokens_details`가 없는 응답(구버전 모델)에서도 터지지 않는가?
- [ ] GlmClient: 응답 구조가 OpenAI와 다를 수 있음. `getattr` 방어 코드가 있는가?
- [ ] OllamaClient: 토큰 정보 없을 때 0으로 일관되게 설정되는가?

### C. 누적 로직 정확성
- [ ] ReactLoop가 iteration마다 정확히 한 번 누적하는가? (오버라이드 실수로 이중 계산 없는가)
- [ ] stop_check/hard_stop 경로에서도 마지막 호출의 토큰이 누락되지 않는가?
- [ ] ScopedReactLoop가 ReactLoop를 상속하므로 누적이 정상 작동하는가?

### D. 파이프라인 집계
- [ ] 각 에이전트(test_writer/implementer/reviewer/intervention)의 토큰이
      role별로 분리되어 저장되는가?
- [ ] Implementer가 retry되는 경우 (같은 role 여러 번 호출) 합산되는가?
- [ ] intervention이 호출되지 않은 경우에도 TaskReport가 정상 생성되는가?

### E. JSONL 로그
- [ ] `agent-data/logs/` 디렉토리가 없을 때 자동 생성되는가?
- [ ] 태스크 실패 시에도 로그 파일이 저장되는가?
- [ ] role 필드가 누락 없이 모든 엔트리에 포함되는가?

### F. 회귀
```bash
pytest tests/ -x --tb=short
```
실행 결과에서 기존 테스트 실패가 있는지 확인한다. 실패가 있으면 반드시 수정 요청.

### G. 계산 검증
작성자가 제공한 샘플 TaskReport의 `cache_hit_rate` 계산이 정의대로인지 검증:
`cached_read / (input + cached_read)`. 0으로 나누기 방어(총합 0이면 0.0)가 있는가?

## 회신 형식
상단의 "공통 검토 원칙"에 명시된 형식으로 작성하라.
특히 다음은 반드시 CHANGES_REQUESTED 사유:
- 기존 테스트 회귀
- `LLMResponse` 기존 필드 삭제/변경
- cached 필드가 누락 시 KeyError/AttributeError 발생 가능
- Ollama 경로에서 예외 발생 가능
````

---

## #3. read_file 부분 읽기 강제

### 배경
- 현재 `read_file(path, start=None, end=None)`에서 start/end 미지정이면 전체 반환.
- ReactLoop 한 사이클에서 같은 파일을 여러 번 전체 읽는 패턴이 관찰됨.
- 200줄 파일 3번 읽기 = 600줄이 sliding window 밖에서도 tool result로 누적.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 다음 작업을 수행하라.

## 목적
`read_file` 도구가 기본값으로 전체 파일을 반환하는 동작을 바꾼다.
큰 파일을 무분별하게 읽는 패턴을 억제하고, 필요 시 명시적으로 추가 읽기를 유도.

## 범위
- `tools/file_tools.py` — `read_file` 함수 시그니처와 동작 변경
- `agents/prompts/*.md` — 역할별 프롬프트에 새 동작 명시
- `tools/registry.py` 또는 스키마 빌더 — 도구 설명(description) 업데이트

## 구체 요구사항

### 1) `read_file` 새 시그니처
```python
def read_file(
    path: str,
    start: int | None = None,
    end: int | None = None,
    max_lines: int = 150,
) -> ToolResult:
    """
    파일을 줄 단위로 읽는다.

    - start, end 둘 다 지정 → 해당 범위
    - start만 지정 → start부터 start + max_lines 까지
    - end만 지정 → 1부터 end 까지
    - 둘 다 None → 1부터 max_lines 까지
      + 파일이 max_lines 초과 시 tool result에
        "⚠️ File has N lines. Showing lines 1-{max_lines}.
          Call read_file(path, start=..., end=...) for the rest."
        힌트를 맨 위에 추가.
    """
```

### 2) ToolResult 포맷
반환 content 맨 앞에 메타데이터 한 줄 추가:
```
=== {path} [lines {start}-{end} of {total}] ===
<실제 내용>
```
LLM이 현재 위치를 파악하기 쉽게 함.

### 3) 경계 처리
- 파일이 존재하지 않음 → 기존 에러 동작 유지
- start > 총 줄 수 → "start exceeds file length (N lines)" 에러
- start > end → "invalid range" 에러
- end > 총 줄 수 → end를 총 줄 수로 clamp하고 경고 추가 (에러 아님)
- max_lines <= 0 → ValueError

### 4) 프롬프트 업데이트
`agents/prompts/test_writer.md`, `implementer.md`, `reviewer.md`에
다음 지침을 명확히 추가:

```
## 파일 읽기 지침
- `read_file`은 기본 150줄까지만 반환합니다.
- 전체 파일이 필요하면 start/end를 명시하세요.
- 가능하면 필요한 부분만 범위 지정하여 읽으세요.
- 검색 목적이면 `search_files` 또는 `list_directory`를 먼저 사용하세요.
```

### 5) 도구 스키마 description 업데이트
`tools/registry.py` 또는 스키마가 정의된 곳에서 `read_file`의 description을
"Read a file with pagination support. Default: first 150 lines."로 변경.

## 하지 말 것
- `read_file_lines`는 건드리지 않는다 (이미 start/end 필수)
- `write_file`, `edit_file`은 건드리지 않는다
- max_lines 기본값을 50 이하로 내리지 말 것 (TestWriter의 pytest 파일 한 개 전체 읽기는 허용돼야 함)

## 테스트 요구사항
1. `tests/test_file_tools.py`에 다음 케이스 추가:
   - 200줄 파일 + 인자 없음 → 150줄 + 경고 헤더
   - 200줄 파일 + start=50 → 50-199줄
   - 200줄 파일 + start=50, end=80 → 50-80줄
   - start > 파일 길이 → 에러
   - start > end → 에러
   - end 초과 → clamp + 경고
2. 기존 read_file 관련 테스트가 깨지지 않는지 확인

## 산출물
- diff 요약
- 변경된 프롬프트 파일들의 diff
- pytest 결과
- 샘플 출력 3개 (인자 없음 / start만 / start+end 각각)
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 `read_file`을
부분 읽기 기본값 방식으로 변경했다. 검토하라.

## 검토 체크리스트

### A. 하위 호환성
- [ ] 기존에 `read_file(path)`로 전체를 받던 호출자가 있다면 동작이 바뀐다.
      레포 전체에서 `read_file(` 호출 지점을 grep하여 영향 범위를 확인했는가?
- [ ] 프롬프트에 지침이 추가되지 않았다면, 에이전트가 새 동작을 모른 채 호출하여
      150줄만 받고 "파일 없음"으로 오해할 위험이 있다. 프롬프트 업데이트가 충분한가?

### B. 경계 케이스
- [ ] 빈 파일 (0줄) → 무엇을 반환하는가? 합리적인가?
- [ ] max_lines를 LLM이 직접 지정한 경우에도 정상 동작하는가?
- [ ] 파일이 정확히 max_lines와 같은 줄 수인 경우 경고가 뜨는가? (뜨면 안 됨)
- [ ] 라인 끝 처리(마지막 줄에 개행 없음)가 안전한가?

### C. ToolResult 포맷
- [ ] 메타데이터 헤더가 모든 경로에서 일관되게 붙는가? (에러 제외)
- [ ] 라인 번호가 1-based인가? (에디터와 일치해야 에이전트가 혼동 없음)

### D. 프롬프트 업데이트
- [ ] test_writer, implementer, reviewer 세 역할 모두에 지침이 추가되었는가?
- [ ] 지침이 에이전트가 실제로 따를 수 있을 만큼 구체적인가?
- [ ] `search_files` 같은 대체 도구로 유도하는 힌트가 있는가?

### E. 도구 스키마
- [ ] description이 새 동작을 정확히 설명하는가?
- [ ] `max_lines` 파라미터도 스키마에 노출되는가? (선택 - 노출하면 에이전트가 조절 가능)

### F. 회귀
```bash
pytest tests/ -x --tb=short
```
특히 `tests/test_file_tools.py` 전체와 기존 E2E 테스트가 통과해야 한다.

### G. 시맨틱 검증
레포 안에서 길이 200줄 이상인 실제 파일 하나 골라서
(예: `core/loop.py`, `orchestrator/pipeline.py`)
`read_file` 호출 시나리오를 수동 실행하여 출력이 자연스러운지 확인하라.

## 회신 형식
상단의 "공통 검토 원칙" 형식을 따른다.
특히 다음은 반드시 CHANGES_REQUESTED:
- 레포 내 기존 호출자가 깨지는데 마이그레이션이 없음
- 프롬프트 업데이트 누락
- 경계 케이스에서 예외 발생
````

---

## #6. TestWriter → Implementer 의도 전달

### 배경
- 현재 TestWriter는 테스트 파일만 남기고 reasoning이 휘발.
- Implementer는 테스트만 보고 의도를 역추적해야 함 → 잘못된 방향으로 구현하거나 테스트 자체를 무력화하는 패턴.
- 해결: TestWriter 종료 시 "design rationale" 1~2단락을 남기고 Implementer 프롬프트에 주입.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 다음 작업을 수행하라.

## 목적
TestWriter가 작업을 마친 후 "테스트 설계 의도"를 짧은 마크다운 문서로 남기고,
Implementer 프롬프트에 이를 주입한다. TDD 핸드오프 시 의도 손실을 보완.

## 범위
- `agents/prompts/test_writer.md` — 마지막 단계로 design_notes 생성 지시 추가
- `orchestrator/pipeline.py` — Implementer 프롬프트 빌더 수정
- `agents/prompts/implementer.md` — design_notes 참조 지침 추가

## 구체 요구사항

### 1) TestWriter 프롬프트 변경
`test_writer.md` 끝에 다음 지시 추가:

```
## 마지막 단계: 설계 노트 작성
모든 테스트 파일을 작성한 후, 반드시 `write_file`로
`context/test_design_notes.md`를 생성하라.

다음 구조를 따른다:

\`\`\`markdown
# Test Design Notes

## 핵심 의도
이 테스트들이 검증하려는 동작을 2-3문장으로 요약.

## 주요 테스트 케이스 설명
- `test_xxx`: 무엇을 어떤 시나리오로 검증하는지 (왜 이 케이스가 중요한지)
- `test_yyy`: ...
  (정상/경계/에러 케이스 각각 최소 1개씩 언급)

## Implementer에게 주는 힌트
- 테스트를 통과하기 위해 주의할 점
- 의도적으로 느슨하게 둔 부분 (구현 자유도)
- 함정이 될 만한 부분

## 가정한 인터페이스
\`\`\`python
class Xxx:
    def method(self, arg: Type) -> ReturnType: ...
\`\`\`
\`\`\`

이 문서는 200줄을 넘지 말 것. 핵심만.
```

### 2) Implementer 프롬프트 수정 (pipeline.py)
`_build_implementer_prompt()`에서 workspace에
`context/test_design_notes.md`가 있으면 프롬프트에 삽입:

```python
design_notes_path = workspace / "context" / "test_design_notes.md"
design_notes_section = ""
if design_notes_path.exists():
    content = design_notes_path.read_text(encoding="utf-8")
    # 너무 긴 경우 잘림 (상한 4000자)
    if len(content) > 4000:
        content = content[:4000] + "\n...(truncated)"
    design_notes_section = f"""
## 테스트 설계 노트 (TestWriter 작성)
{content}

위 노트를 먼저 읽고 의도를 파악한 뒤 구현하라.
테스트 파일(tests/)은 노트만으로 부족할 때 직접 read_file로 확인하라.
"""
```

프롬프트 구성 순서:
```
[기존] 태스크 제목/설명
[기존] 수락 기준
[기존] workspace 경로
[기존] target_files
[NEW] design_notes_section  ← 여기
[기존] "tests/를 먼저 읽고 구현하라" 지시
[기존] (재시도 시) task.last_error
[기존] (reviewer 피드백 시) reviewer_feedback
```

### 3) Implementer 프롬프트 업데이트 (implementer.md)
```
## 구현 시작 절차
1. `context/test_design_notes.md`가 있으면 먼저 읽어라. 테스트의 의도를 파악하는 데 도움이 된다.
2. `tests/` 디렉토리의 파일을 필요에 따라 read_file로 확인.
3. 구현 시작.

⚠️ design_notes는 힌트일 뿐 계약이 아니다. 실제 계약은 tests/ 파일이다.
design_notes와 tests/가 충돌하면 tests/를 따른다.
```

### 4) ScopedReactLoop 워크스페이스 생성 보정
TestWriter의 `role.allowed_tools`에 `write_file`이 이미 포함되어 있어야 함 (확인 필요).
`context/` 디렉토리가 workspace에 존재하는지 확인. 없으면
`WorkspaceManager.create()` 또는 `inject_dependency_context()` 근처에서
`mkdir(parents=True, exist_ok=True)` 처리 (이미 있을 수 있으니 확인만).

## 하지 말 것
- TestWriter의 기존 pytest 파일 생성 동작 변경 금지
- Reviewer 프롬프트 건드리지 않음 (이번 스코프 외)
- design_notes를 필수로 만들지 말 것 (없어도 동작해야 함)

## 테스트 요구사항
1. `tests/test_pipeline_handoff.py` 신규:
   - design_notes가 있을 때 Implementer 프롬프트에 포함되는지
   - 4000자 초과 시 잘리는지
   - design_notes가 없을 때도 프롬프트가 정상 생성되는지
2. E2E 테스트 하나 실행 (기존 유틸 모듈 재생성 시나리오).
   실제로 TestWriter가 `context/test_design_notes.md`를 생성하는지 확인.

## 산출물
- diff 요약
- 실제 E2E 실행 결과에서 생성된 `test_design_notes.md` 샘플 1개
- pytest 결과
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 TestWriter→Implementer
의도 전달 기능을 구현했다. 검토하라.

## 검토 체크리스트

### A. 강제성 vs 유연성
- [ ] design_notes가 없을 때도 파이프라인이 정상 동작하는가? (기존 회귀 방지)
- [ ] TestWriter가 notes 작성을 건너뛰어도 실패하지 않는가?
      (강제 안 하는 게 맞다 — 프롬프트에서 유도만)

### B. 프롬프트 설계 품질
- [ ] test_writer.md의 지시가 에이전트가 실제로 유용한 notes를 쓰도록 유도하는가?
      (단순 "설명하세요"가 아닌 구조화된 섹션 제시)
- [ ] implementer.md에 "notes와 tests/가 충돌하면 tests/를 따른다"는 명시가 있는가?
      (이 명시 없으면 테스트를 임의 수정하는 risk)

### C. 주입 위치
- [ ] Implementer 프롬프트 내 design_notes 삽입 위치가 적절한가?
      (target_files 뒤, "tests/ 읽어라" 지시 앞이 권장)
- [ ] 4000자 제한이 합리적인가? 너무 짧으면 힌트가 부족, 너무 길면 토큰 낭비.

### D. 파일 시스템
- [ ] `context/` 디렉토리가 이미 존재할 수도/없을 수도 있다. mkdir 방어 코드가 있는가?
- [ ] TestWriter의 allowed_tools에 write_file이 있는지 확인했는가?
- [ ] design_notes 경로에 path traversal 가능성은 없는가?

### E. Reviewer와의 상호작용
- [ ] Reviewer 프롬프트에 design_notes 참조 지시가 **없는지** 확인.
      (이번 스코프 외 — 추가됐다면 스코프 침범)
- [ ] Reviewer가 design_notes를 읽어도 괜찮지만, 읽으라고 명시적으로 유도하면 안 됨.

### F. 회귀
- [ ] 기존 E2E 테스트 (계산기, 단어 빈도 등)가 여전히 통과하는가?
- [ ] design_notes를 쓰지 않는 TestWriter 시나리오도 통과하는가?
      (과거 tasks.yaml 재실행으로 검증 가능)

### G. 실제 샘플 검토
작성자가 제공한 `test_design_notes.md` 샘플을 읽어보고 다음 판단:
- 이 문서가 Implementer에게 **실제로 도움이 되는가**?
- 아니면 쓸데없는 정보만 담긴 "형식적 문서"인가?
- 후자라면 test_writer.md 프롬프트의 구조화 지시를 더 강화해야 한다.

## 회신 형식
상단의 "공통 검토 원칙" 형식을 따른다.
특히 다음은 반드시 CHANGES_REQUESTED:
- design_notes 없을 때 파이프라인 실패
- "tests/가 계약이다" 명시 누락 (Implementer가 테스트를 고칠 수 있음)
- path traversal 취약점
````

---

## #2. OpenAI/GLM Stable Prefix로 캐시 적중률 향상

### 배경
- OpenAI의 자동 prompt caching은 prefix가 byte-identical일 때만 발동.
- 현재 `_to_openai_messages()`가 system을 messages 배열 첫 항목으로 삽입하는데,
  system_prompt 자체가 매 호출 동일하면 자동 캐싱이 걸려야 함.
- 그러나 Message 객체 → dict 변환 과정에서 미묘한 차이(공백, 키 순서 등)가 생길 수 있고,
  tool 스키마가 매번 재생성되면 prefix가 깨짐.
- **#1 완료 후**에만 진행 — cached_read_tokens로 효과 직접 측정 가능해야 함.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 전제: 작업 #1 (token logging)이 이미 완료되어
`cached_read_tokens`가 측정 가능한 상태다.

## 목적
OpenAI와 GLM 클라이언트의 API 호출 메시지 구조를 안정화(stable prefix)하여
자동 prompt caching 적중률을 높인다.

## 사전 조사 (반드시 먼저 수행)
작업 시작 전 다음을 `web_search`로 확인:
1. OpenAI prompt caching 2026년 기준 최소 토큰 임계값
   (과거 1024 토큰이었으나 변경됐을 수 있음)
2. GLM/Zai의 prompt caching 공식 지원 여부
   (지원하지 않으면 이 작업은 OpenAI에만 적용)

조사 결과를 커밋 메시지 또는 PR 설명에 기록.

## 범위
- `llm/openai_client.py`, `llm/glm_client.py`
- `core/loop.py` — tools 스키마 캐싱 (매 호출 재생성 방지)
- 필요 시 `agents/scoped_loop.py`

## 구체 요구사항

### 1) Tools 스키마 안정화
`ReactLoop.__init__`에서 `self.TOOLS_SCHEMA = get_tools_schema()`를
**한 번만** 호출하도록 되어 있는지 확인. 매 iteration 재생성하면 prefix 깨짐.
ScopedReactLoop의 `_build_scoped_schema()`도 `__init__`에서 한 번만 실행하도록 보장.

### 2) Message 변환 결정성 (determinism)
`_to_openai_messages()` (openai_client.py, glm_client.py 각각)에서:
- dict 키 순서를 고정 (`role`, `content`, `tool_calls`, `tool_call_id` 순)
- content가 str일 때 `.strip()` 등의 전처리를 하지 말 것
  (원본 그대로 유지해야 캐시 적중)
- 동일한 입력에 대해 항상 동일한 JSON 직렬화 결과가 나오는지
  `json.dumps(msg, sort_keys=False)` 기준으로 확인

### 3) System 메시지 안정화
system_prompt가 파이프라인 단위로 일정한지 확인:
- 현재 코드에서 시스템 프롬프트 내에 타임스탬프, UUID, 랜덤 요소가
  포함되어 있지 않은지 검증
- 있다면 제거하거나 messages 뒤쪽으로 이동

### 4) 호출 메시지 레이아웃 문서화
`llm/openai_client.py` 상단에 docstring으로 메시지 구조 명시:

```
API 호출 시 messages 배열 구조 (stable prefix 최대화):

  [0]   system (불변)
  [1]   첫 user 메시지 (태스크 기술 — 루프 내 불변)
  [2..] assistant / tool_result 턴 (가변)

앞 [0]~[1]이 캐싱되어 재사용됨. 슬라이딩 윈도우로 [2..]가 잘리면
[0]~[1]은 여전히 동일하므로 캐시 유효.
```

### 5) 검증 스크립트
`scripts/verify_cache_hit.py` 신규:
동일한 system_prompt + 동일한 user 메시지로 2회 연속 OpenAI API 호출하고,
2번째 호출의 `cached_tokens` 값을 출력. 0이면 캐시 적중 실패 경고.

실행 방법:
```bash
python scripts/verify_cache_hit.py --provider openai --model gpt-4.1-mini
```

## 하지 말 것
- `llm/claude_client.py`는 건드리지 않음 (이미 명시적 cache_control 사용 중)
- 기존 캐싱 마커를 OpenAI에 추가하려 하지 말 것 (OpenAI는 자동만 지원)
- system_prompt 내용 자체는 수정하지 않음

## 테스트 요구사항
1. `tests/test_openai_message_layout.py` 신규:
   - 동일 입력에 대해 `_to_openai_messages()` 출력이 byte-identical인지
     (`json.dumps` 비교)
   - tool_calls가 없을 때와 있을 때 키 순서가 일관되는지
2. `tests/test_glm_message_layout.py` 동일
3. 실제 API 호출 검증은 `scripts/verify_cache_hit.py`로 수동 실행 (CI에 넣지 말 것)

## 산출물
- web_search 조사 결과 요약
- diff
- pytest 결과
- `scripts/verify_cache_hit.py` 실제 실행 결과 (cached_tokens 증가 확인)
- 수동 시나리오: 같은 태스크를 2번 연속 실행하고 2번째의 token_usage에서
  cached_read 비율이 첫 번째보다 유의미하게 높은지 비교
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 OpenAI/GLM 캐시 적중률
향상 작업을 수행했다. 검토하라.

## 검토 체크리스트

### A. 사전 조사의 정확성
- [ ] 작성자가 web_search로 확인한 OpenAI 캐싱 임계값이 **현재 기준**인가?
      (당신이 별도로 web_search해서 교차 검증하라)
- [ ] GLM 캐싱 지원 여부 결론이 정확한가?
      (GLM 공식 문서를 직접 확인)

### B. 결정성 검증
- [ ] `_to_openai_messages()`가 진짜 byte-identical을 보장하는가?
      작성자가 제공한 테스트가 **순서 불변성**을 검증하는가, 아니면
      단순히 내용 일치만 보는가? 전자여야 맞다.
- [ ] dict 키 순서 고정이 Python 3.7+ 삽입 순서 보존에 의존하는가?
      의존한다면 명시적으로 `collections.OrderedDict` 또는
      명시적 dict 생성 순서 규약을 코드에 표시.

### C. Tools 스키마 캐싱
- [ ] `TOOLS_SCHEMA`가 `__init__`에서 정확히 한 번만 생성되는지 코드 추적.
- [ ] ScopedReactLoop 인스턴스가 여러 번 생성되는 경우 스키마가 공유되는가 아니면 재생성되는가?
      (재생성돼도 내용이 동일하면 OK — 단, 내용 동일성이 보장되는지 확인)

### D. 실제 효과 검증
- [ ] `scripts/verify_cache_hit.py` 실행 결과가 제출됐는가?
- [ ] 2번째 호출의 `cached_tokens`가 0이 아닌가?
- [ ] cached_tokens 값이 기대치에 부합하는가?
      (system + 첫 user가 대략 N토큰이면 N의 80% 이상이 캐시돼야 함)

### E. 회귀
```bash
pytest tests/ -x --tb=short
```
특히 `test_openai_client.py`, `test_glm_client.py` 전체 통과 확인.

### F. ClaudeClient 불변성
- [ ] 작성자가 실수로 `claude_client.py`를 수정하지 않았는가? (스코프 외)
- [ ] ClaudeClient의 cache_control 로직이 여전히 동작하는가?

### G. 놓친 prefix 파괴 지점
다음을 추가로 검증:
- [ ] `extra` 필드에 타임스탬프나 request_id가 포함돼 호출마다 달라지진 않는가?
- [ ] 도구 결과 포맷에서 timestamp/uuid가 tool_result content에 들어가진 않는가?
- [ ] Message.content가 list인 경우(tool_use, tool_result) 내부 키 순서가 안정적인가?

## 회신 형식
상단의 "공통 검토 원칙" 형식을 따른다.
특히 다음은 반드시 CHANGES_REQUESTED:
- 실제 캐시 적중 검증 결과 누락 (`verify_cache_hit.py` 실행 로그 없음)
- 작성자의 조사 결과가 오래된/잘못된 정보 기반
- ClaudeClient 스코프 침범
- dict 키 순서 의존성이 숨겨진 가정으로만 존재 (명시되지 않음)
````

---

## #4. Retry 시 직전 시도 요약 주입

### 배경
- 현재 Implementer retry 시 이전 ReactLoop의 모든 reasoning이 휘발.
- 받는 것은 `task.last_error[:2000]` (docker stdout 잘린 것)뿐.
- 결과: 같은 가설로 같은 함정에 다시 빠지는 실패 패턴.
- Task-007, 023이 이 패턴으로 $0.5를 날리고 실패한 것으로 추정.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 전제: #1 (token logging)이 이미 완료되어
실패 태스크의 reasoning trace가 JSONL 로그로 보존되는 상태다.

## 목적
Implementer가 재시도(retry)될 때, 직전 시도가 **무엇을 시도했고 왜 실패했는지**를
요약한 문서를 프롬프트에 주입하여 동일한 실수를 반복하지 않도록 한다.

## 범위
- `orchestrator/pipeline.py` — Implementer 재호출 직전에 요약 LLM 호출 추가
- `orchestrator/intervention.py` — 기존 intervention 로직과 통합 (중복 방지)
- 새 파일: `orchestrator/retry_summary.py` — 요약 생성 로직 분리

## 구체 요구사항

### 1) `retry_summary.py` 신규
```python
def summarize_previous_attempt(
    task: Task,
    previous_loop_log: list[dict],   # ReactLoop.call_log (JSONL 엔트리)
    failure_reason: str,
    docker_stdout: str,              # task.last_error
    llm_client,                      # 빠른 모델 사용 (fast 프로바이더)
) -> str:
    """
    직전 Implementer 시도의 압축 요약을 반환.

    출력 포맷 (마크다운, 800자 이내):

    ## 직전 시도 요약 (시도 {N})
    ### 무엇을 시도했나
    - {핵심 구현 전략 한두 줄}

    ### 수행한 주요 액션
    - {도구 호출 시퀀스 요약: "utils.py를 3번 읽고 src/core.py에 X 클래스 정의"}

    ### 왜 실패했나
    - {구체 원인: 테스트명, 에러 메시지 핵심}

    ### 이번 시도에서 피해야 할 것
    - {반복하면 안 될 함정 — 직전 시도의 reasoning을 기반으로}
    """
```

요약 LLM 호출 시:
- 모델: 요약 전용 저비용 role model 또는 별도 저비용 기본 모델 (오케스트레이터 기본 모델 아님. 비용 절감)
- 입력: call_log에서 도구 이름 시퀀스 + assistant 메시지 텍스트 일부 추출
  (raw JSONL을 그대로 넣지 말고 압축된 형태로 정제)
- max_tokens: 600

### 2) 파이프라인 통합
`pipeline.py`의 Implementer 재호출 경로에서:
```python
if retry_count > 0:
    # 직전 Implementer 루프의 call_log가 보존되어 있어야 함 (#1 작업 결과)
    prev_log = self._last_implementer_call_log  # 새 인스턴스 변수
    summary = summarize_previous_attempt(
        task=task,
        previous_loop_log=prev_log,
        failure_reason=last_failure_reason,
        docker_stdout=task.last_error or "",
        llm_client=self.fast_client,
    )
    task.retry_summary = summary  # Task dataclass에 새 필드
```

### 3) Implementer 프롬프트에 주입
`_build_implementer_prompt()`에서 `task.retry_summary`가 있으면 삽입:
```
[기존 섹션들]

## 직전 시도에서 배운 것
{retry_summary}

이 요약을 바탕으로 다른 접근을 시도하라.
단, 직전 접근이 부분적으로 맞았다면 그 부분은 유지해도 된다.
```

위치: reviewer_feedback 바로 앞 (또는 뒤 — 어느 쪽이든 일관성만 유지).

### 4) intervention.py와의 관계
기존 intervention은 `max_orchestrator_retries` 초과 시 상위 개입이고,
이번 retry_summary는 그 아래 레벨(Implementer 자체 retry)에서 동작.

**중복 방지**: intervention이 힌트를 주입하는 경우, retry_summary는 스킵하거나
두 힌트를 통합(concatenate + 중복 제거)하여 한 번만 프롬프트에 포함.
어느 쪽을 선택했는지 주석으로 명시.

### 5) Task dataclass 확장
```python
@dataclass
class Task:
    # 기존 필드들
    ...
    retry_summary: str | None = None  # 신규
```

YAML 저장 시에는 휘발성이므로 제외 (저장하지 않음).

### 6) 비용 고려
요약 LLM 호출은 retry마다 1회 추가됨 = 비용 증가.
예상: 입력 ~2000 토큰, 출력 ~400 토큰, 저비용 요약 모델 기준 ~$0.005 수준.
이 비용이 "잘못된 retry 방지"로 상쇄되는지 작성자 판단 근거를 PR 설명에 기록.

## 하지 말 것
- 요약 프롬프트를 오케스트레이터급 고비용 모델로 돌리지 말 것 (비용 과다)
- call_log 전체를 raw로 넣지 말 것 (토큰 폭발)
- 요약을 위한 추가 요약 재귀는 금지 (1 레벨만)
- 첫 시도(retry_count == 0)에서 요약 호출 금지

## 테스트 요구사항
1. `tests/test_retry_summary.py`:
   - 요약 생성 함수의 mock LLM으로 포맷 검증
   - 빈 call_log일 때 에러 없이 fallback 요약 반환
   - retry_count == 0일 때 호출되지 않음을 파이프라인 레벨에서 검증
2. E2E: 의도적으로 실패할 태스크 하나 작성하여 (예: 잘못된 acceptance_criteria)
   retry 시 summary가 주입되는지 로그로 확인

## 산출물
- diff
- E2E 실행 로그 (retry 주입 시점 확인)
- 실제 생성된 retry_summary 샘플 1개
- 비용 영향 추정 (PR 설명)
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 retry 시 직전 시도
요약 주입 기능을 구현했다. 검토하라.

## 검토 체크리스트

### A. 아키텍처 정합성
- [ ] intervention과 retry_summary의 역할 분리가 명확한가?
      (전자: 여러 retry 후 상위 개입. 후자: 매 retry마다 하위 힌트)
- [ ] 둘이 동시에 활성화되는 경우 프롬프트에 중복 힌트가 들어가지 않는가?
- [ ] call_log 보존 책임이 어디에 있는가? 메모리 누수 위험은?

### B. 비용 타당성
- [ ] 작성자가 제시한 비용 추정이 합리적인가?
      (요약 호출 $0.005 × retry_count 평균 0.3회 = 태스크당 $0.0015 추가)
- [ ] 이 비용이 실패 태스크 비용 절감으로 상쇄되는가?
      작성자는 정량적 근거를 제시했는가?
- [ ] 저비용 요약 모델이 아닌 경로로 호출되지 않도록 검증했는가?

### C. 요약 품질
- [ ] 작성자가 제공한 샘플 요약이 실제로 다음 시도에 **유용**한가?
      아니면 형식만 채운 내용 없는 요약인가?
- [ ] 요약 프롬프트 자체가 "무엇을 시도했나" / "왜 실패했나"를
      명확히 구분하도록 설계되었는가?

### D. 에지 케이스
- [ ] call_log가 비어 있는 경우 (LLM 호출 전 즉시 실패)?
- [ ] call_log가 너무 긴 경우 (예: 50 iteration) 사전 압축 로직이 있는가?
- [ ] retry_summary 생성 자체가 실패하면 (LLM 에러) 파이프라인이 죽지 않고
      graceful degradation 되는가? (None으로 두고 진행)

### E. 프롬프트 위치
- [ ] design_notes (#6 작업) + retry_summary + reviewer_feedback이 동시에 있을 때
      프롬프트 구조가 여전히 명확한가? 서로 충돌하지 않는가?
- [ ] "직전 접근이 부분적으로 맞았다면 유지해도 된다"는 문구가
      에이전트에게 올바른 자유도를 주는가?

### F. 회귀
```bash
pytest tests/ -x --tb=short
```
특히:
- 기존 E2E 태스크(계산기 등)에서 첫 시도 성공 시 요약이 호출되지 않음
- retry 시나리오에서 요약이 정확히 1회만 호출됨

### G. 로그/관찰성
- [ ] retry_summary 생성 시 debug 로그가 남는가?
      ("retry_summary generated for task-XXX, {N} tokens")
- [ ] call_log 입력이 제대로 압축되는지 볼 수 있는가?

## 회신 형식
상단의 "공통 검토 원칙" 형식을 따른다.
특히 다음은 반드시 CHANGES_REQUESTED:
- 오케스트레이터급 고비용 모델로 요약이 호출되는 경로 존재
- call_log가 압축 없이 raw로 전달 (토큰 폭발)
- intervention과 중복 힌트 주입 방지 로직 없음
- 요약 LLM 에러 시 파이프라인 크래시
````

---

## #5. Sliding Window → 시맨틱 Auto-Compaction

### 배경
- 현재 `_trim_history()`는 단순 고정 윈도우(기본 6턴). 의미 고려 없음.
- 오래된 tool result가 "중요한 결정 맥락"일 수 있는데 무차별적으로 잘림.
- 반대로 낡은 read_file 결과가 앞쪽에 남아 있기도 함.
- 해결: context가 임계 토큰 수 초과 시 작은 LLM 호출로 "중간 요약" 생성 후
  오래된 verbose 메시지 드롭, 요약을 대체 삽입.
- **#1, #2 완료 후**에 진행 — 가장 높은 위험.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 전제: #1 (token logging),
#2 (cache stability)가 모두 완료되어 캐시 적중률과 토큰을 측정 가능한 상태다.

## 목적
ReactLoop의 고정 슬라이딩 윈도우 방식을 **시맨틱 auto-compaction**으로 대체.
context가 임계치를 넘으면 작은 LLM으로 "지금까지의 진행 요약"을 만들어
오래된 tool result들을 드롭하고 요약 메시지로 대체.

## 전제 조건 확인
시작 전 다음을 확인:
- [ ] #1 토큰 로깅이 동작하여 per-call input_tokens 측정 가능
- [ ] #2 캐시 안정화로 baseline 캐시 적중률 측정됨
위 둘 중 하나라도 미완료면 작업 중단하고 보고.

## 범위
- `core/loop.py` — `_trim_history()` 대체 또는 보강
- 새 파일: `core/compactor.py` — 요약 로직 분리
- `agents/scoped_loop.py` — ScopedReactLoop에서도 동작 확인

## 구체 요구사항

### 1) `compactor.py` 신규
```python
@dataclass
class CompactionResult:
    summary_message: Message   # 요약을 담은 새 user 메시지
    dropped_range: tuple[int, int]  # (start_idx, end_idx) — messages에서 드롭된 구간
    input_tokens_used: int
    output_tokens_used: int

def compact_history(
    messages: list[Message],
    llm_client,              # 저비용 요약 모델
    keep_first_n: int = 2,   # system, 첫 user 메시지는 보존
    keep_last_n: int = 4,    # 최근 N 메시지(2 턴)는 보존
) -> CompactionResult:
    """
    messages 배열의 가운데 구간을 요약하여 단일 user 메시지로 대체.

    반환 요약 메시지 구조:
        role: "user"
        content: "[이전 대화 요약]
                  {LLM이 생성한 요약 텍스트}
                  ----
                  (이하 최근 대화 이어서)"
    """
```

요약 LLM 프롬프트:
```
다음은 코드 작성 에이전트의 작업 히스토리다. 이 히스토리를 300단어 이내로 요약하라.
포함할 것:
- 지금까지 읽은 주요 파일과 그 핵심 내용
- 수정/생성한 파일과 그 이유
- 테스트 실행 결과의 핵심 (실패 테스트명, 에러 요지)
- 현재 진행 중인 작업과 다음 의도

생략할 것:
- 성공한 단순 파일 읽기 로그
- 전체 스택 트레이스 (핵심 한 줄만)
- 도구 스키마 정보
```

### 2) `core/loop.py` 통합
`_trim_history()` 완전 대체가 아닌 **점진적 도입**:

```python
class ReactLoop:
    def __init__(
        self,
        ...,
        compaction_threshold_tokens: int = 30000,   # 입력 토큰 이 초과 시 트리거
        compaction_enabled: bool = True,
        fast_client_for_compaction = None,          # None이면 self.llm 사용
    ):
```

매 iteration 후 (도구 결과 append 뒤):
```python
estimated_tokens = sum(estimate_tokens(m) for m in messages)
if (self.compaction_enabled
    and estimated_tokens > self.compaction_threshold_tokens):
    result = compact_history(messages, self.fast_client_for_compaction or self.llm)
    # 드롭된 구간을 요약 메시지로 대체
    messages[result.dropped_range[0]:result.dropped_range[1]] = [result.summary_message]
    self._log_compaction(result)   # call_log에 "compaction" 엔트리 추가
```

### 3) Token 추정
`estimate_tokens(message)` — 정확한 tokenization 대신 `len(text) / 4` 추정 충분.
단, content가 list(tool_use/tool_result)인 경우 모든 내부 문자열 합산.

### 4) 메시지 경계 존중
`compact_history` 호출 시 **assistant-user 페어가 중간에 잘리지 않도록** 주의:
- dropped_range의 시작은 assistant 메시지여야 함
- 끝은 그 assistant에 대응하는 user(tool_result) 메시지여야 함
- Anthropic API는 tool_use 뒤에 반드시 tool_result가 와야 함 —
  이 구조를 깨면 API 에러 발생

### 5) 기존 sliding window와의 관계
기본값으로는 `compaction_enabled=True`, `_trim_history` 경로는 제거.
단, `context_pruner`(기존 커스텀 pruner) 주입 경로는 유지 — 우선순위는 pruner > compaction.

### 6) 회귀 방지 플래그
환경 변수 `DISABLE_COMPACTION=1`으로 끌 수 있게 함.
운영 중 문제 시 즉시 롤백 가능.

### 7) 관찰성
compaction 발생 시 로그:
```
[compaction] task=XXX iter=N: dropped 15 messages (12000 tokens)
             → 1 summary (400 tokens). net savings: 11600 tokens.
             compaction cost: 500 in + 300 out tokens.
```

## 하지 말 것
- 첫 user 메시지(태스크 설명) 절대 드롭하지 말 것 — 캐시 prefix가 깨짐
- system 메시지 드롭 금지
- compaction 중 tool_use/tool_result 페어 깨지 말 것
- 오케스트레이터급 고비용 모델로 요약 호출 금지

## 테스트 요구사항
1. `tests/test_compactor.py`:
   - 임계치 미만 → compaction 미발동
   - 임계치 초과 → compaction 발동, 메시지 개수 감소, 앞뒤 보존
   - tool_use + tool_result 페어가 드롭 경계에서 깨지지 않는지
   - system + 첫 user 메시지가 절대 드롭되지 않는지
2. `tests/test_loop_with_compaction.py`:
   - ReactLoop가 compaction 후에도 정상 종료 이벤트를 내는지
   - compaction을 유발한 다음 호출에서 캐시 적중이 살아있는지
     (첫 두 메시지가 보존되므로 적중해야 함)
3. E2E: 실제 태스크 중 의도적으로 read_file을 많이 유발하는 것으로
   compaction 트리거 후 완료되는지 확인

## 산출물
- diff
- pytest 결과
- E2E 실행 로그 (compaction 발생 시점 + 토큰 절감 수치)
- A/B 비교: 동일 태스크를 `DISABLE_COMPACTION=1`과 default로 각각 1회 실행.
  total_tokens와 cache_hit_rate 비교 표 첨부
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 sliding window를
시맨틱 auto-compaction으로 대체했다. 가장 위험도 높은 작업이다. 엄격히 검토하라.

## 검토 체크리스트

### A. 메시지 무결성 (가장 중요)
- [ ] compaction 후 messages 배열에서 **tool_use 뒤에 반드시 tool_result**
      규칙이 유지되는가? 샘플 배열에서 직접 추적 검증.
- [ ] system 메시지가 절대 드롭되지 않는가?
- [ ] 첫 user 메시지(messages[1])가 절대 드롭되지 않는가?
      (Anthropic 캐시 prefix 때문에 중요)
- [ ] 요약 메시지의 role이 "user"인가?
      ("assistant"면 다음 턴에 또 assistant가 와서 API 에러)

### B. 캐시 생존성
- [ ] compaction 후 첫 호출에서 cache_read_tokens > 0인가?
      (A/B 비교 로그에서 확인)
- [ ] compaction 자체가 캐시를 깨뜨리진 않는가?
      (system + 첫 user 보존 원칙이 지켜지면 OK)

### C. 비용 대비 효과
- [ ] compaction 호출 비용 vs 절감 토큰의 balance가 양의 값인가?
      (로그에서 "compaction cost"와 "net savings" 비교)
- [ ] 임계치(30000)가 합리적인가?
      너무 낮으면 compaction 과다 호출, 너무 높으면 효과 미미.

### D. 에지 케이스
- [ ] compaction 직후 또 다른 compaction이 연쇄 호출되는 경우가 있는가?
      (요약 직후 바로 재compaction 방지 로직 필요)
- [ ] compaction LLM 호출 자체가 실패하면 어떻게 되는가?
      (graceful degradation: sliding window fallback? 또는 그냥 진행?)
- [ ] 매우 짧은 메시지가 많을 때 `keep_last_n`이 정확히 적용되는가?

### E. 기존 path와의 호환
- [ ] `context_pruner`가 주입된 경우 compaction이 비활성화되는가?
- [ ] `DISABLE_COMPACTION=1`이 실제로 동작하는가? 테스트로 검증됐는가?
- [ ] ScopedReactLoop에서도 정상 동작하는가?

### F. 관찰성
- [ ] 로그 형식이 사용자 요청 사양과 일치하는가?
- [ ] call_log JSONL에 compaction 엔트리가 추가되어 사후 분석 가능한가?

### G. A/B 비교 결과
작성자가 제공한 A/B 비교 표를 다음 관점에서 평가:
- [ ] 비교 조건이 동일한가? (같은 태스크, 같은 초기 상태)
- [ ] total_tokens가 실제로 줄었는가?
- [ ] cache_hit_rate가 유지되거나 향상되었는가?
- [ ] 태스크 성공 여부가 동일한가? (compaction이 실패를 유발하진 않는가)

만약 A/B 결과가 불리하거나 태스크가 실패했다면, `DISABLE_COMPACTION=1` 기본값으로
변경하고 alpha flag로 두는 것을 권고.

### H. 회귀
```bash
DISABLE_COMPACTION=1 pytest tests/ -x
```
그리고 그 다음:
```bash
pytest tests/ -x
```
두 실행 모두 통과해야 한다.

### I. 코드 품질
- [ ] `estimate_tokens`의 추정치가 실제 API 청구치와 크게 벗어나지 않는가?
      (len/4 추정이 tool_use JSON에 대해 얼마나 정확한지 확인)
- [ ] compaction 경계 선택 로직이 읽기 쉬운가? (복잡한 인덱싱은 주석 필수)

## 회신 형식
상단의 "공통 검토 원칙" 형식을 따른다.
이 작업은 다음 중 **하나라도** 해당하면 반드시 CHANGES_REQUESTED:
- tool_use/tool_result 페어가 경계에서 깨질 가능성
- A/B 결과에서 태스크 실패
- system 또는 첫 user 메시지 드롭 가능성
- DISABLE_COMPACTION fallback 미구현
- cache_hit_rate가 유의미하게 하락
````

---

## #7. 태스크 초안 컨텍스트 풍부화 (_DRAFT_SYSTEM_PROMPT 강화)

### 배경
- 현재 회의 LLM(Opus)이 풍부한 reasoning으로 `context_doc.md`를 생성하지만, 초안 생성 LLM이 이를 `tasks.yaml`로 변환할 때 배경 정보 대부분이 소멸.
- TestWriter는 description과 acceptance_criteria만 보고 테스트를 짜므로:
  - "왜 이 기능이 필요한지" 모름 → 의미 있는 엣지 케이스를 놓침
  - "인접 태스크와의 관계" 모름 → 인터페이스 설계가 엉뚱해짐
  - "프로젝트 차원의 제약/컨벤션" 모름 → 다른 모듈과 일관성 깨짐
  - "명시적 비고려 항목" 모름 → 스코프 밖 테스트를 짜거나 과잉 구현 유도
- Implementer도 동일한 결핍: "왜"를 모르는 개발자가 더 나쁜 코드를 짬.
- Reviewer도 "이게 의도적 생략인지 버그인지" 구분 못 함.
- `_DRAFT_SYSTEM_PROMPT` 강화로 description 자체를 풍부하게 만들면 **한 번의 변경으로 전 에이전트가 혜택**.

### 작성 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 다음 작업을 수행하라.

## 목적
태스크 초안 생성 시 LLM이 각 태스크의 description을 더 풍부하게 작성하도록
`_DRAFT_SYSTEM_PROMPT`를 강화한다. 모든 에이전트(TestWriter, Implementer, Reviewer)가
각 태스크의 "왜", "맥락", "제약"을 알고 작업할 수 있게 한다.

## 범위
- `backend/routers/tasks.py` — `_DRAFT_SYSTEM_PROMPT` 수정
- `backend/routers/tasks.py` — `_sanitize_task_draft()` 필요 시 보정 로직 추가

## 현재 description 작성 문제점

현재 초안 생성이 만드는 description 예시 (실제 사례):
```
메트릭 수집기를 구현한다. Task Report를 YAML 형식으로 저장하고,
로드하고, 집계하는 기능을 제공한다.
```

이 description으로 TestWriter가 알 수 있는 것: "저장/로드/집계 기능을 만들면 됨".
알 수 없는 것: 왜 이게 필요한지, 누가 소비하는지, 어떤 형식이어야 하는지,
다른 모듈과 어떻게 연동되는지, 무엇을 하지 말아야 하는지.

## 개선된 description 구조 (4 섹션)

`_DRAFT_SYSTEM_PROMPT`에 다음 구조를 태스크별 description에 포함하도록 지시:

```
### 목적과 배경
이 태스크가 왜 필요한지, 전체 시스템에서 어떤 역할인지 2-3문장으로 설명.
context_doc에서 이 기능의 동기나 배경 논의가 있었다면 핵심만 발췌.

### 기술 요구사항
구체적인 구현 요구사항. 기존 acceptance_criteria의 역할을 흡수.
입력/출력 형식, 데이터 구조, 알고리즘 제약 등.

### 인접 컨텍스트
- 이 태스크의 결과를 사용하는 후속 태스크: {depends_on 역참조}
- 이 태스크가 의존하는 선행 태스크의 산출물: {depends_on 정참조}
- 관련 파일/모듈 (이미 존재하는 것): {있다면}

### 비고려 항목 (Out of Scope)
이 태스크에서 의도적으로 다루지 않는 것.
- 예: "DB 연동은 이 태스크의 범위가 아님. 파일 기반 저장만."
- 예: "인증/권한은 다루지 않음."
```

## `_DRAFT_SYSTEM_PROMPT` 수정 사항

기존 프롬프트의 description 작성 지시 부분을 찾아서 위 4 섹션 구조를
명시적으로 요구하도록 교체. 구체적으로:

### 1) description 작성 규칙 추가
```
각 태스크의 description은 다음 4개 섹션을 반드시 포함해야 합니다:

1. **목적과 배경**: 이 태스크가 왜 필요한지, 프로젝트 전체에서 어떤 역할인지.
   컨텍스트 문서에서 관련 논의가 있었다면 핵심 결정만 발췌하세요.
   "무엇을 만드는지"가 아니라 "왜 만드는지"를 먼저 설명하세요.

2. **기술 요구사항**: 구체적인 구현 스펙. 입출력 형식, 데이터 구조, 인터페이스 제약.
   acceptance_criteria와 겹쳐도 괜찮습니다 — criteria는 테스트 가능한 형태,
   여기는 구현자가 이해할 수 있는 산문 형태로 쓰세요.

3. **인접 컨텍스트**: 이 태스크의 결과를 누가 사용하는지(후속 태스크 ID + 제목),
   이 태스크가 의존하는 선행 산출물은 무엇인지(선행 태스크 ID + 산출물).
   프로젝트에 이미 존재하는 관련 파일이 있다면 경로를 명시하세요.

4. **비고려 항목**: 이 태스크에서 의도적으로 다루지 않는 것을 명시하세요.
   이것이 없으면 에이전트가 범위 밖 기능까지 구현하려 합니다.
   context_doc에서 "나중에 한다"고 결정된 것이 있으면 여기에 적으세요.
```

### 2) description 길이 가이드라인
```
각 태스크의 description은 400~800자(한국어 기준) 범위를 목표로 하세요.
200자 미만이면 에이전트가 맥락 부족으로 잘못된 방향으로 갈 위험이 큽니다.
1000자를 초과하면 요약하세요.
```

### 3) acceptance_criteria와의 관계
```
acceptance_criteria는 기존대로 테스트 가능한 체크리스트로 유지하세요.
description의 "기술 요구사항" 섹션과 내용이 겹쳐도 됩니다.
criteria는 "무엇이 통과해야 하는지", description은 "왜 그래야 하는지".
```

### 4) context_doc 참조 지시
```
컨텍스트 문서(context_doc.md)에서 각 태스크와 관련된 결정 사항이 있다면
description의 "목적과 배경" 섹션에 핵심만 발췌하세요.
폐기된 대안은 포함하지 마세요 — 채택된 결정만.
"컨텍스트 문서에 따르면..." 같은 메타 참조 대신 내용 자체를 직접 쓰세요.
```

## _sanitize_task_draft() 보정

### description 최소 길이 체크
보정 로직에 다음 추가:
```python
if len(task.get("description", "")) < 100:
    warnings.append(
        f"task-{task['id']}: description이 100자 미만입니다. "
        "목적과 배경, 기술 요구사항, 비고려 항목이 포함되어야 합니다."
    )
```
경고만 추가하고 실패는 아님 (LLM이 간결하게 잘 쓸 수도 있으므로).

### 섹션 존재 여부 체크
```python
expected_sections = ["목적", "기술 요구", "인접", "비고려"]
desc = task.get("description", "")
missing = [s for s in expected_sections if s not in desc]
if missing:
    warnings.append(
        f"task-{task['id']}: description에 권장 섹션이 누락됨: {missing}"
    )
```
역시 경고만, 실패 아님.

## 하지 말 것
- acceptance_criteria 형식이나 역할 변경 금지
- target_files 로직 변경 금지
- _sanitize_task_draft()의 기존 보정(Python 파일명 변환 등) 건드리지 않음
- description에 코드 블록 강제하지 말 것 (에이전트가 소비하는 텍스트이므로 산문이 더 나음)
- 회의 대화 원문을 description에 삽입하지 말 것 (context_doc에서 **결정 사항만** 발췌)

## 테스트 요구사항

### 1) 프롬프트 품질 테스트 (수동)
실제 context_doc.md 하나로 POST /api/tasks/draft 호출 → 생성된 tasks.yaml의
각 태스크 description이 4 섹션 구조를 따르는지 육안 확인.

### 2) _sanitize_task_draft 보정 테스트
`tests/test_task_draft_sanitize.py`에 다음 추가:
- description 100자 미만 → warning 포함
- 권장 섹션 누락 → warning 포함
- 기존 보정(Python 변환 등) 동작이 깨지지 않음

### 3) 생성 품질 비교
동일한 context_doc.md로 변경 전/후 초안을 각각 1회 생성하여
description 품질 차이를 비교한 문서를 제출.

비교 기준:
- 4섹션 구조가 자연스러운가
- "왜"가 명확히 전달되는가
- 비고려 항목이 실제로 스코프를 제한하는 데 유용한가
- 길이가 과도하지 않은가

## 산출물
- `_DRAFT_SYSTEM_PROMPT` diff
- `_sanitize_task_draft()` diff
- 변경 전 vs 변경 후 description 비교 문서 (동일 context_doc 기준)
- pytest 결과
- 실제 생성된 tasks.yaml 샘플 1개 (전체)
````

### 검토 프롬프트

````
당신은 이 레포의 시니어 엔지니어다. 방금 다른 에이전트가 태스크 초안 생성의
description 풍부화 작업을 수행했다. 검토하라.

## 검토 체크리스트

### A. 프롬프트 설계 품질 (가장 중요)

작성자가 제출한 "변경 후 description 샘플"을 각 에이전트 관점에서 평가:

- [ ] **TestWriter 관점**: 이 description을 읽고 어떤 테스트를 짤지 상상하라.
      "목적과 배경"이 의미 있는 엣지 케이스 발견에 도움이 되는가?
      "비고려 항목"이 불필요한 테스트 작성을 실제로 억제하는가?

- [ ] **Implementer 관점**: 이 description을 읽고 어떤 코드를 짤지 상상하라.
      "인접 컨텍스트"가 인터페이스 설계에 실질적 도움을 주는가?
      (예: "이 함수의 반환값을 task-003이 JSON으로 직렬화한다" → 직렬화 가능한 타입 설계 유도)

- [ ] **Reviewer 관점**: 코드 리뷰 시 "이건 빠진 건가 의도적 생략인가?"를
      "비고려 항목" 섹션으로 판별할 수 있는가?

- [ ] **과잉 정보 체크**: description이 에이전트를 오히려 혼란스럽게 만드는
      불필요한 배경을 포함하고 있지 않은가?
      (예: 회의에서 폐기된 대안 상세, 프로젝트와 무관한 개인적 동기 등)

### B. 프롬프트 구조적 정확성
- [ ] 기존 `_DRAFT_SYSTEM_PROMPT`의 다른 지시(target_files 규칙, Python 구현 원칙,
      task_type 분류 등)가 훼손되지 않았는가?
- [ ] 새 description 지시와 기존 acceptance_criteria 지시 사이에 모순이 없는가?
- [ ] "400~800자" 가이드라인이 4 섹션을 모두 넣었을 때 현실적인가?
      (너무 짧으면 섹션이 형식만 되고 내용이 없음)

### C. _sanitize_task_draft 보정
- [ ] 경고가 warning이지 error가 아닌지 확인 (description 짧다고 초안 생성 실패하면 안 됨)
- [ ] 기존 보정 로직(Python 변환, PascalCase→snake_case 등)이 그대로 동작하는가?
- [ ] "섹션 존재 여부 체크"의 키워드 매칭이 한국어/영어 혼용 상황에서 작동하는가?
      (LLM이 영어로 "Purpose and Background"라고 쓰면 매칭 실패)

### D. 비용 영향 추정
- [ ] description이 길어지면 모든 에이전트의 입력 토큰이 증가한다.
      기존 description 평균 길이 vs 변경 후 평균 길이를 비교했는가?
      증가분 × 에이전트 3개 × 평균 iteration 수 = 태스크당 추가 토큰 추정.
- [ ] 이 추가 비용이 "더 나은 첫 시도 성공률"로 상쇄되는가에 대한
      작성자의 판단이 포함되어 있는가?

### E. 변경 전후 비교 검증
작성자가 제출한 비교 문서에서:
- [ ] 동일한 context_doc.md를 사용했는가? (다른 문서면 비교 무의미)
- [ ] 변경 후 description이 4 섹션 구조를 **자연스럽게** 따르는가?
      (형식만 채우고 내용이 없는 "형식적 충족"은 실패)
- [ ] context_doc에 없는 정보를 LLM이 환각으로 만들어 넣진 않았는가?
      (특히 "비고려 항목"에서 — context_doc에서 결정되지 않은 것을
      LLM이 임의로 비고려로 선언하면 위험)

### F. 회귀
```bash
pytest tests/ -x --tb=short
```
특히 `tests/test_task_draft_sanitize.py` 전체 통과 확인.

### G. 실제 초안 생성 테스트 (가능하면)
시간이 허용하면: 실제 context_doc.md로 POST /api/tasks/draft 호출하여
- 생성된 모든 태스크의 description이 최소 3/4 섹션 포함하는지
- 생성 시간이 유의미하게 길어지지 않았는지 (프롬프트가 너무 길어서 생성 지연)

## 회신 형식
상단의 "공통 검토 원칙" 형식을 따른다.
특히 다음은 반드시 CHANGES_REQUESTED:
- 기존 _DRAFT_SYSTEM_PROMPT의 다른 지시 훼손
- description에 환각 정보 삽입 방지 장치 없음
- 기존 보정 로직 회귀
- 섹션 키워드 매칭이 한국어만/영어만 고정되어 다른 언어 무시
````

---

## 부록: 전체 작업 사용 가이드

### 실행 순서 및 상호 의존성

```
#1 (token logging)
   ↓ 측정 인프라 확보
#3 (read_file 부분 읽기)   ── 독립, 빠르고 안전
   ↓
#6 (TestWriter 의도 전달)   ── 독립, 빠르고 안전
   ↓
#7 (태스크 컨텍스트 풍부화)  ── 독립, #6과 시너지
   ↓
#2 (OpenAI/GLM 캐시 안정화)  ── #1 필요 (cache_read_tokens 측정)
   ↓
#4 (retry summary)           ── #1 필요 (call_log)
   ↓
#5 (auto-compaction)         ── #1, #2 모두 필요 (가장 위험)
```

### 검토 통과 후 다음 작업 진행 판단 기준

각 작업 완료 시 다음을 충족하면 다음 단계로 진행:

| 작업 | 다음 작업 진행 조건 |
|------|---------------------|
| #1 | 실제 태스크 1회 실행 후 TaskReport에 token_usage 필드가 정확히 기록됨. JSONL 로그 파일이 생성되고 role별 엔트리 포함 확인. |
| #3 | E2E 테스트 1회 통과. read_file 호출에서 150줄 기본 동작 로그 확인. 토큰 절감치를 #1 데이터로 측정 (전후 비교). |
| #6 | E2E에서 실제 `test_design_notes.md` 1개 생성 확인. Implementer가 해당 파일을 참조하는 로그 1건 이상 확인. |
| #7 | 실제 context_doc으로 초안 생성 후, 생성된 description 중 75% 이상이 4섹션 중 3개 이상 포함. 변경 전 description 대비 길이 2배 이상 증가. 환각 비고려 항목이 0건. |
| #2 | `verify_cache_hit.py` 2회차 호출에서 cached_tokens > 0 확인. 동일 태스크 2회 실행 시 2회차의 cache_hit_rate가 첫 회 대비 유의미 상승. |
| #4 | retry 유발 태스크에서 retry_summary가 Implementer 프롬프트에 포함됨을 로그로 확인. 오케스트레이터급 고비용 모델이 아닌 저비용 요약 모델로 호출됨을 token_usage에서 확인. |
| #5 | A/B 비교에서 default(compaction on)가 baseline(`DISABLE_COMPACTION=1`) 대비 total_tokens 감소, cache_hit_rate 유지, 태스크 성공 동일. 불리하면 기본값 off로 두고 stop. |

### 검토 결과 템플릿

검토자는 반드시 다음 형식으로 회신:

```markdown
## 검토 결과: APPROVED / CHANGES_REQUESTED

### 잘 된 점
- (구체적으로)

### 반드시 수정 (CHANGES_REQUESTED 사유)
- `파일:줄` — 문제 — 제안

### 권장 사항 (non-blocking)
- ...

### 테스트 실행 결과
```bash
$ pytest tests/ -x
(결과 붙여넣기)
```

### 추가 관찰
(작성자 프롬프트의 체크리스트 외 발견한 사항)
```
