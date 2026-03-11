## 멀티 에이전트 통신 방식

**중앙 오케스트레이터 패턴**

```
사용자
  ↓
Orchestrator  ←── 얘가 두뇌
  ↓      ↓      ↓
Planner  Coder  Reviewer
```

처음부터 에이전트끼리 직접 소통하게 만들면 디버깅이 지옥이에요. 오케스트레이터가 중간에서 조율하면 흐름 추적이 쉽고, 나중에 확장도 편해요.

---

## 전체 디렉토리 구조

```
local-coding-agent/
│
├── main.py                      # 진입점 (CLI 실행)
│
├── config/
│   ├── config.yaml              # 모델, 경로 등 전역 설정
│   └── agents.yaml              # 각 에이전트 역할/프롬프트 정의
│
├── core/                        # 핵심 엔진
│   ├── __init__.py
│   ├── orchestrator.py          # 작업 분배 & 에이전트 조율
│   ├── agent.py                 # 에이전트 베이스 클래스
│   └── loop.py                  # ReAct 루프 (Reason→Act→Observe)
│
├── agents/                      # 전문화된 에이전트들
│   ├── __init__.py
│   ├── planner.py               # 작업 분석 & 계획 수립
│   ├── coder.py                 # 코드 생성 & 수정
│   ├── reviewer.py              # 코드 리뷰 & 버그 탐지
│   └── executor.py              # 터미널 명령 실행 전담
│
├── tools/                       # 에이전트가 쓰는 도구들
│   ├── __init__.py
│   ├── file_tools.py            # 파일 읽기/쓰기/수정
│   ├── shell_tools.py           # 터미널 명령 실행
│   ├── code_tools.py            # AST 파싱, 코드 분석
│   └── registry.py              # 도구 등록 & 관리
│
├── llm/                         # LLM 연동 레이어
│   ├── __init__.py
│   ├── base.py                  # LLM 베이스 인터페이스
│   └── ollama_client.py         # Ollama 연동
│
├── memory/                      # 세션 & 히스토리 관리
│   ├── __init__.py
│   ├── session.py               # 세션 생성/불러오기
│   └── db.py                    # SQLite CRUD
│
├── cli/                         # 사용자 인터페이스
│   ├── __init__.py
│   ├── interface.py             # 입출력, 색상, 포맷팅
│   └── commands.py              # /help, /history 같은 슬래시 명령
│
├── data/
│   └── sessions.db              # SQLite 파일 (자동 생성)
│
├── tests/
│   ├── test_tools.py
│   ├── test_agents.py
│   └── test_loop.py
│
├── pyproject.toml               # 패키지 & 의존성 관리
└── README.md
```

---

## 각 폴더의 역할 한 줄 요약

| 폴더 | 한 줄 설명 |
|------|-----------|
| `core/` | 에이전트를 움직이는 엔진. 가장 먼저 만들어요 |
| `agents/` | 역할별 전문가. Planner가 계획하면 Coder가 실행 |
| `tools/` | 에이전트의 "손". 파일/터미널/코드 분석 |
| `llm/` | Ollama 교체해도 이 레이어만 바꾸면 됨 |
| `memory/` | 대화 기억. SQLite로 세션 영속성 확보 |
| `cli/` | 사용자가 보는 부분. 맨 나중에 다듬어요 |

---

## 개발 순서 (이 구조 기준)

```
1단계: llm/ → tools/ → core/loop.py    (단일 에이전트 동작)
2단계: memory/ → cli/                   (쓸 만한 CLI 완성)
3단계: agents/ → core/orchestrator.py  (멀티 에이전트)
```
