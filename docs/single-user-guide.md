# AI Coding Agent 사용 설명서

> 싱글 코딩 에이전트 사용 가이드

---

## 시작하기

### 실행

```bash
# 기본 (일반 모드)
python main.py

# provider / model 지정
python main.py -p claude  -m claude-opus-4-6
python main.py -p openai  -m gpt-4o
python main.py -p ollama  -m devstral:24b
python main.py -p gemini  -m gemini-2.5-pro-preview-06-05
python main.py -p glm     -m glm-5.1

# 모델명 prefix로 provider 자동 추론
python main.py -m glm-5.1
python main.py -m claude-sonnet-4-6

# 이전 세션 이어하기 (세션 ID 앞 몇 자리만 입력)
python main.py -s a1b2c3d4

# 사용 가능한 모델 목록 조회
python main.py --list
python main.py -p openai --list

# 디버그 로그 출력
python main.py -v
```

CLI 옵션이 없으면 일반 REPL 설정은 `~/.config/ai_coding_agent/config.toml`을 사용합니다.
TDD 모드용 설정은 별도의 `agent.toml` 계층을 사용합니다.

### 종료

- `/exit` 또는 `/quit` 입력
- `Ctrl-C`

---

## 설정 파일

CLI에는 두 종류의 설정 파일이 있습니다.

### 1) 일반 REPL 설정: `~/.config/ai_coding_agent/config.toml`

일반 모드의 LLM, 루프 반복 수, 도구 승인 기본값을 제어합니다. CLI 인자가 항상 우선합니다.

```toml
provider = "claude"           # claude | openai | ollama | gemini | glm
model = "claude-sonnet-4-6"
max_iterations = 15
max_tokens = 4096
auto_approve = false          # true 이면 모든 도구 자동 승인 (승인 프롬프트 없음)
```

파일이 없거나 파싱 오류가 나면 기본값으로 동작합니다.

### 2) TDD 모드 설정: `agent.toml`

TDD 모드는 현재 git 프로젝트 루트의 `agent.toml`을 읽습니다. 전역 기본값은
`~/.config/agent/config.toml`에 둘 수 있습니다.

우선순위는 다음과 같습니다.

1. 환경 변수
2. 현재 프로젝트의 `agent.toml`
3. `~/.config/agent/config.toml`
4. 하드코딩 기본값

지원 환경 변수:

- `LLM_PROVIDER`
- `LLM_MODEL_FAST`
- `LLM_MODEL_CAPABLE`
- `LLM_PROVIDER_FAST`
- `LLM_PROVIDER_CAPABLE`
- `LLM_TITLE_MODEL` (`LLM_MODEL_FAST`가 없을 때 fallback)
- `LLM_DEFAULT_MODEL` (`LLM_MODEL_CAPABLE`가 없을 때 fallback)

예시:

```toml
[llm]
provider = "claude"
model_fast = "claude-haiku-4-5-20251001"
model_capable = "claude-opus-4-6"

[project]
language = "python"
test_framework = "pytest"
base_branch = "main"

[behavior]
default_mode = "normal"  # "normal" | "tdd"
auto_push = false
```

`agent.toml`이 없으면 기본값으로 계속 진행합니다. 파싱 오류가 나도 프로그램은 종료되지 않고,
stderr에 경고를 출력한 뒤 기본값으로 fallback 합니다.
`default_mode = "tdd"`를 설정했더라도 현재 위치가 git 프로젝트 밖이면 일반 모드로 시작합니다.

---

## 입력 방식

### 일반 모드

기본 모드입니다. 프롬프트에 자연어로 작업을 입력하면 ReAct 루프가 도구를 사용해 처리합니다.

```text
[a1b2c3d4] ❯ main.py에서 TODO 주석 전부 찾아줘
[a1b2c3d4] ❯ tests/ 디렉토리에 있는 테스트 전부 실행해줘
[a1b2c3d4] ❯ 방금 만든 함수에 타입 힌트 추가해줘
```

응답이 끝나면 사용한 토큰 수(`input / output / total`)가 자동으로 표시됩니다.

### TDD 모드

TDD 모드에서는 입력 한 줄이 일반 대화로 가지 않고, 단일 태스크 파이프라인으로 바로 라우팅됩니다.

```text
[TDD] [a1b2c3d4] ❯ 로그인 실패 케이스에 대한 pytest 추가하고 구현까지 마무리해줘
```

실행 흐름:

1. 자연어 입력을 TaskConverter가 단일 Task로 정리
2. 태스크 요약 출력
3. 사용자 확인
4. TDD 파이프라인 실행
5. 결과 카드 출력
6. 성공 시 로컬 git commit까지 진행

현재 CLI 통합은 `FULL_TDD` 모드로 동작하며, 개념적으로 다음 단계를 거칩니다.

1. TestWriter
2. DockerTest
3. Quality Gate
4. Implementer
5. DockerTest
6. Reviewer

TDD 모드는 git 프로젝트 안에서만 사용할 수 있습니다. 현재 디렉토리에서 상위로 `.git`을 찾지 못하면
TDD 전환이 차단되고 다음 안내가 표시됩니다.

```text
경고: .git을 찾지 못했습니다. TDD 모드는 git 프로젝트 내에서 실행하세요.
```

이 상태에서 `/tdd`, `/mode tdd`, `Shift+Tab`은 모두 차단됩니다.

### 모드 전환

- `Shift+Tab`: 일반 ↔ TDD 전환
- `/tdd`: TDD 모드로 전환
- `/normal`: 일반 모드로 전환
- `/mode`: 현재 모드 확인
- `/mode tdd`
- `/mode normal`

이미 해당 모드라면 전환하지 않고 안내만 출력합니다.

TDD 모드에서도 슬래시 명령어는 계속 동작합니다. 예를 들어 `/help`, `/sessions`, `/history`,
`/load`는 모드와 무관하게 사용할 수 있습니다.

### 이전 입력 불러오기

- `↑` / `↓` 방향키로 이전에 입력한 내용을 탐색할 수 있습니다.
- 세션이 유지되는 동안 히스토리가 보존됩니다.

---

## @ 파일/디렉토리 첨부

`@경로`를 입력하면 해당 파일이나 디렉토리 내용이 메시지에 자동으로 포함됩니다.
에이전트에게 특정 파일을 직접 보여주고 싶을 때 사용합니다.

### 파일 첨부

```text
[a1b2c3d4] ❯ @core/loop.py 이 파일에서 성능 문제가 있을 만한 부분 찾아줘
[a1b2c3d4] ❯ @src/auth.py @src/models.py 두 파일 사이에 순환 의존성이 있는지 확인해줘
```

에이전트에게 전달되는 실제 메시지:
```text
이 파일에서 성능 문제가 있을 만한 부분 찾아줘

[파일: core/loop.py]
<python file contents>
...파일 내용...
```

### 디렉토리 트리 첨부

```
[a1b2c3d4] ❯ @tools/ 이 폴더 구조 파악하고 registry.py 역할 설명해줘
[a1b2c3d4] ❯ @. 전체 프로젝트 구조 보고 어떤 파일부터 읽어야 할지 알려줘
```

디렉토리는 트리 형태로 주입됩니다 (`.git`, `.venv`, `__pycache__` 등 자동 제외).

### 제한 사항

| 항목 | 제한 |
|------|------|
| 단일 파일 최대 크기 | 500 KB (초과 시 생략 경고 표시) |
| 디렉토리 최대 항목 수 | 200개 |
| 존재하지 않는 경로 | `@경로` 원문 그대로 LLM에게 전달 |

---

## 탭 자동완성

`Tab` 키를 누르면 현재 입력에 맞는 후보가 표시됩니다.

### @ 경로 자동완성

`@` 뒤에 경로를 타이핑하다 `Tab`을 누르면 파일/디렉토리 목록이 표시됩니다.

```
[a1b2c3d4] ❯ @cor[Tab]
──────────────────
  core/
```

```
[a1b2c3d4] ❯ @core/[Tab]
──────────────────
  __init__.py
  loop.py
  context.py
  undo.py
  config.py
```

### / 명령어 자동완성

`/` 뒤에 `Tab`을 누르면 사용 가능한 명령어 목록이 표시됩니다.

```text
[a1b2c3d4] ❯ /[Tab]
────────────────────────────────────────────
  /delete    /exit      /help      /history
  /load      /mode      /new       /normal
  /quit      /rename    /sessions  /tdd
  /undo
```

```text
[a1b2c3d4] ❯ /hi[Tab]  →  /history
```

---

## 슬래시 명령어

에이전트 대화와 별개로 세션을 관리하는 명령어입니다.

| 명령어 | 설명 |
|--------|------|
| `/help` | 명령어 목록 출력 |
| `/history` | 현재 세션의 대화 히스토리 출력 |
| `/sessions` | 저장된 세션 전체 목록 출력 |
| `/new [제목]` | 새 세션 시작 |
| `/load <id>` | 세션 ID 앞자리로 세션 불러오기 |
| `/rename <제목>` | 현재 세션 제목 변경 |
| `/delete` | 현재 세션 삭제 후 새 세션 시작 |
| `/undo` | 마지막 파일 변경 되돌리기 |
| `/undo all` | 이번 세션의 모든 파일 변경 되돌리기 |
| `/mode` | 현재 모드 확인 |
| `/mode tdd` | TDD 모드로 전환 |
| `/mode normal` | 일반 모드로 전환 |
| `/tdd` | TDD 모드로 전환 |
| `/normal` | 일반 모드로 전환 |
| `/exit` | 종료 |

### 예시

```text
# 새 세션을 이름 붙여서 시작
[a1b2c3d4] ❯ /new 리팩토링 작업

# 세션 목록 확인
[a1b2c3d4] ❯ /sessions

# 특정 세션으로 전환
[a1b2c3d4] ❯ /load a1b2

# 현재 세션 제목 변경
[a1b2c3d4] ❯ /rename 인증 모듈 구현

# 마지막 파일 변경 되돌리기
[a1b2c3d4] ❯ /undo

# 이번 세션 전체 파일 변경 되돌리기
[a1b2c3d4] ❯ /undo all

# TDD 모드 전환
[a1b2c3d4] ❯ /tdd

# 현재 모드 확인
[a1b2c3d4] ❯ /mode
```

---

## TDD 모드 상세

### 태스크 확인과 재시도

TDD 모드는 일반 도구 승인 프롬프트 대신, 단계별 인라인 선택기를 사용합니다.

- 태스크 검토: 진행 / 이 세션에서 항상 허용 / 취소
- 태스크가 너무 큰 경우: 진행 / 취소
- 테스트 실패 시: 재시도 / 힌트 추가해서 재시도 / 중단
- 리뷰가 `CHANGES_REQUESTED`인 경우: 피드백 반영해서 재시도 / 무시하고 진행 / 중단
- 파이프라인 오류 시: 재시도 / 중단

### 중단 방법

- `Ctrl-C`: 현재 TDD 파이프라인 중단
- `Q`: 파이프라인 실행 중 중단 요청
- 미니 회의나 힌트 입력 프롬프트에서 `Esc` 또는 빈 입력: 취소

### 적합한 작업

TDD 모드는 “단일 태스크를 받아 테스트-구현-리뷰까지 끝내는 흐름”에 적합합니다.

```text
[TDD] [a1b2c3d4] ❯ user_service.py의 deactivate_user에 대한 실패 테스트 먼저 추가하고 구현해줘
[TDD] [a1b2c3d4] ❯ 토큰 만료 처리 버그를 재현하는 테스트를 만들고 수정까지 마무리해줘
```

반대로 긴 탐색형 대화, 구조 설명, 코드 리뷰만 요청하는 경우는 일반 모드가 더 맞습니다.

---

## 도구 승인 시스템

파일 수정, 셸 명령 실행, git commit 등 되돌리기 어려운 작업은 실행 전에 승인을 요청합니다.

```
⚠  파일 수정 요청: core/loop.py
--- a/core/loop.py
+++ b/core/loop.py
@@ -31,1 +31,1 @@
-_APPROVAL_REQUIRED = {"write_file", "edit_file"}
+_APPROVAL_REQUIRED = {"write_file", "edit_file", "execute_command"}

  승인하시겠습니까? [Y/n/a(항상)]:
```

| 입력 | 동작 |
|------|------|
| `Y` 또는 Enter | 이번 한 번 승인 |
| `n` | 거부 (에이전트에게 취소 알림) |
| `a` | 항상 승인 (세션 동안 같은 도구 자동 실행) |

승인된 파일 수정은 `/undo`로 되돌릴 수 있습니다.

`config.toml`에 `auto_approve = true`를 설정하면 승인 프롬프트 없이 모든 도구가 자동 실행됩니다.

---

## 도구 목록

에이전트가 자율적으로 사용하는 도구들입니다.

### 파일

| 도구 | 설명 |
|------|------|
| `read_file` | 파일 전체 내용 읽기 |
| `read_file_lines` | 파일의 특정 줄 범위만 읽기 (대용량 파일) |
| `write_file` | 파일 생성 또는 전체 덮어쓰기 |
| `edit_file` | 파일의 특정 문자열만 교체 |
| `append_to_file` | 파일 끝에 내용 추가 |
| `list_directory` | 디렉토리 목록 조회 |
| `search_in_file` | 단일 파일 내 정규식 검색 |
| `search_files` | 디렉토리 전체 재귀 정규식 검색 |

### 코드 분석

| 도구 | 설명 |
|------|------|
| `get_imports` | Python 파일의 import 문 추출 |
| `get_outline` | Python 파일의 함수·클래스 구조 요약 |
| `get_function_src` | 특정 함수·메서드 소스코드 추출 |

### 셸

| 도구 | 설명 |
|------|------|
| `execute_command` | 셸 명령어 실행 (테스트, 빌드 등) |

### Git

| 도구 | 설명 |
|------|------|
| `git_status` | 워킹 트리 상태 확인 |
| `git_diff` | 변경 사항 diff 확인 (staged 옵션 지원) |
| `git_log` | 최근 커밋 로그 확인 |
| `git_add` | 파일 스테이징 |
| `git_commit` | 커밋 생성 (승인 필요) |

---

## 세션

대화 내용은 SQLite (`data/sessions.db`)에 자동 저장됩니다.

- 실행 시 `--session` 옵션으로 이전 세션을 이어갈 수 있습니다.
- 세션 ID는 8자리 앞자리만 입력해도 됩니다.
- 세션 관리 명령(`/sessions`, `/load`, `/rename`, `/delete`)은 일반 모드와 TDD 모드에서 모두 사용할 수 있습니다.

```bash
# 세션 목록 확인 후 이어하기
python main.py
[a1b2c3d4] ❯ /sessions
[a1b2c3d4] ❯ /load b9f1

# 또는 시작 시 바로 지정
python main.py -s b9f1
```

---

## 활용 예시

### 버그 수정

```
[a1b2c3d4] ❯ @core/loop.py 도구 타임아웃이 발생했을 때 루프가 멈추는 문제가 있어. 원인 찾고 고쳐줘
```

### 코드 리뷰

```
[a1b2c3d4] ❯ @tools/file_tools.py 이 파일 코드 리뷰해줘. 예외 처리 위주로 봐줘
```

### 테스트 작성

```
[a1b2c3d4] ❯ @memory/session.py 이 모듈에 대한 pytest 테스트 작성해줘
```

### 프로젝트 탐색

```
[a1b2c3d4] ❯ @. 전체 구조 보고 llm/ 레이어가 어떻게 동작하는지 설명해줘
```

### Git 작업

```
[a1b2c3d4] ❯ 현재 변경사항 확인하고 의미 있는 단위로 커밋해줘
```

### 여러 파일 비교

```
[a1b2c3d4] ❯ @llm/claude_client.py @llm/ollama_client.py 두 클라이언트의 구현 방식 차이점 정리해줘
```
