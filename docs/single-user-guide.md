# AI Coding Agent 사용 설명서

> 싱글 코딩 에이전트 사용 가이드

---

## 시작하기

### 실행

```bash
# 기본 (config 파일 또는 Claude Sonnet)
python main.py

# 모델 지정
python main.py -p claude  -m claude-opus-4-6
python main.py -p openai  -m gpt-4o
python main.py -p ollama  -m devstral:24b

# 이전 세션 이어하기 (세션 ID 앞 몇 자리만 입력)
python main.py -s a1b2c3d4

# 디버그 로그 출력
python main.py -v
```

CLI 옵션이 없으면 `~/.config/ai_coding_agent/config.toml` 설정을 사용합니다.

### 종료

- `/exit` 또는 `/quit` 입력
- `Ctrl-C`

---

## 설정 파일

`~/.config/ai_coding_agent/config.toml` 에 기본값을 저장할 수 있습니다. CLI 인자가 항상 우선합니다.

```toml
provider = "claude"           # claude | openai | ollama
model = "claude-sonnet-4-6"
max_iterations = 15
max_tokens = 4096
auto_approve = false          # true 이면 모든 도구 자동 승인 (승인 프롬프트 없음)
```

파일이 없거나 파싱 오류가 나면 기본값으로 동작합니다.

---

## 입력 방식

### 기본 대화

프롬프트에 자연어로 작업을 입력하면 에이전트가 도구를 사용해 처리합니다.

```
[a1b2c3d4] ❯ main.py에서 TODO 주석 전부 찾아줘
[a1b2c3d4] ❯ tests/ 디렉토리에 있는 테스트 전부 실행해줘
[a1b2c3d4] ❯ 방금 만든 함수에 타입 힌트 추가해줘
```

응답이 끝나면 사용한 토큰 수(input / output / total)가 자동으로 표시됩니다.

### 이전 입력 불러오기

- `↑` / `↓` 방향키로 이전에 입력한 내용을 탐색할 수 있습니다.
- 세션이 유지되는 동안 히스토리가 보존됩니다.

---

## @ 파일/디렉토리 첨부

`@경로`를 입력하면 해당 파일이나 디렉토리 내용이 메시지에 자동으로 포함됩니다.
에이전트에게 특정 파일을 직접 보여주고 싶을 때 사용합니다.

### 파일 첨부

```
[a1b2c3d4] ❯ @core/loop.py 이 파일에서 성능 문제가 있을 만한 부분 찾아줘
[a1b2c3d4] ❯ @src/auth.py @src/models.py 두 파일 사이에 순환 의존성이 있는지 확인해줘
```

에이전트에게 전달되는 실제 메시지:
```
이 파일에서 성능 문제가 있을 만한 부분 찾아줘

[파일: core/loop.py]
```python
...파일 내용...
```
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

```
[a1b2c3d4] ❯ /[Tab]
──────────────────────────────
  /delete    /exit    /help
  /history   /load    /new
  /quit      /rename  /sessions
  /undo
```

```
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
| `/exit` | 종료 |

### 예시

```
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
```

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
