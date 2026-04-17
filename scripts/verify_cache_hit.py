"""
scripts/verify_cache_hit.py — prompt caching 적중률 수동 검증 스크립트.

OpenAI 또는 GLM 에 동일한 system_prompt + user 메시지로 2회 연속 호출하고,
2번째 호출의 `cached_tokens` 값을 출력한다.

OpenAI 의 자동 prompt caching 은 prefix 가 1024 토큰 이상일 때만 작동하므로,
이 스크립트는 충분히 긴 더미 system_prompt(~1500 토큰) 로 패딩한다.

사용법:
    python scripts/verify_cache_hit.py --provider openai --model gpt-4.1-mini
    python scripts/verify_cache_hit.py --provider glm --model glm-4.5-air

전제:
    OPENAI_API_KEY 또는 ZAI_API_KEY 가 .env 에 설정되어 있어야 한다.

해석:
    OpenAI/GLM 의 prompt caching 은 best-effort 다. 같은 prefix 라도 호출마다
    다른 replica 로 라우팅되면 cached_read 가 0 이 될 수 있고, 반대로 기존에
    캐시가 warm 했다면 1회차부터 높은 hit 가 나올 수 있다. 이 스크립트는
    "2회차가 1회차보다 더 증가했는가" 같은 연속 증가량을 보지 않는다.

    성공 기준:
      2회 호출 중 "최댓값" cached_read 가 prefix 의 30% 또는 1024 토큰 중
      큰 값을 넘으면 prefix 가 캐시 가능한 형태로 전송되고 있다는 증거로
      간주한다. 한 호출이라도 높은 hit 가 보이면 prefix 안정성은 충분히
      입증된다 (provider 측 라우팅/이전 warm 여부와 무관).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llm.base import LLMConfig, Message  # noqa: E402


# 1024 토큰 임계값을 넘기기 위한 더미 system prompt (실제 시스템 프롬프트를
# 대체하지는 않고, 단순히 "길고 안정적인 prefix" 를 만드는 데만 쓰인다).
_PADDED_SYSTEM = (
    "당신은 로컬 파일 시스템에서 작동하는 코딩 에이전트입니다. "
    "다음 원칙을 철저히 따르세요:\n\n"
    + "\n".join(
        f"- 원칙 {i}: 항상 명확한 도구 호출을 우선하고, 불필요한 추측을 피하고, "
        "결과를 요약할 때는 간결한 한국어로 사용자에게 전달합니다. "
        "도구 실행 결과가 나오기 전에는 결론을 내리지 말고, "
        "에러 발생 시 원인 분석 후 다른 방식으로 재시도하세요."
        for i in range(1, 60)
    )
)

_USER_MSG = (
    "간단한 덧셈 함수 `add(a, b)`를 Python으로 작성하고 설명해주세요. "
    "한두 줄 정도의 답으로 충분합니다."
)


def _make_client(provider: str, model: str):
    cfg = LLMConfig(model=model, temperature=0.0, max_tokens=256,
                    system_prompt=_PADDED_SYSTEM)
    if provider == "openai":
        from llm.openai_client import OpenaiClient
        return OpenaiClient(cfg)
    if provider == "glm":
        from llm.glm_client import GlmClient
        return GlmClient(cfg)
    raise ValueError(f"지원하지 않는 provider: {provider} (openai | glm)")


def _one_call(client, label: str) -> tuple[int, int]:
    """한 번 호출하고 (input_tokens, cached_read_tokens) 반환."""
    t0 = time.perf_counter()
    resp = client.chat([Message(role="user", content=_USER_MSG)])
    elapsed = (time.perf_counter() - t0) * 1000
    print(
        f"[{label}] input={resp.input_tokens:>6}  "
        f"output={resp.output_tokens:>4}  "
        f"cached_read={resp.cached_read_tokens:>6}  "
        f"({elapsed:.0f}ms)"
    )
    return resp.input_tokens, resp.cached_read_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["openai", "glm"], required=True)
    parser.add_argument("--model", required=True,
                        help="예: gpt-4.1-mini, glm-4.5-air")
    args = parser.parse_args()

    print(f"▶ provider={args.provider}  model={args.model}")
    print(f"  system_prompt 길이: {len(_PADDED_SYSTEM)} chars "
          f"(~{len(_PADDED_SYSTEM) // 4} tokens 추정)")
    print()

    client = _make_client(args.provider, args.model)

    # 동일 prefix 로 2회 호출한다. 각각이 cold 인지 warm 인지는 provider 측
    # 라우팅과 이전 캐시 상태에 따라 달라지며, 여기서 그 내부 상태를 알 수 없다.
    in1, cached1 = _one_call(client, "1회차")
    in2, cached2 = _one_call(client, "2회차")

    print()
    print("─" * 60)
    # 판정: 2회 호출 중 "어느 한 번이라도" cached_read 가 prefix 의 30% 또는
    # 1024 토큰(OpenAI 최소 블록 크기) 중 큰 값을 넘었는가.
    # prompt caching 이 best-effort 라는 점을 반영해 "연속 증가" 가 아니라
    # "at least one hit" 로 성공을 정의한다.
    best = max(cached1, cached2)
    input_ref = max(in1, in2, 1)
    threshold = max(1024, int(input_ref * 0.3))
    if best >= threshold:
        ratio = best / input_ref * 100
        print(
            f"✅ 캐시 가능: 2회 호출 중 최대 cached={best} "
            f"({ratio:.1f}% of input). prefix 가 안정적으로 캐시되고 있음."
        )
        return 0
    else:
        print(
            f"⚠️ 캐시 미적중 또는 불충분: max(cached1={cached1}, cached2={cached2})"
            f" < threshold({threshold}).\n"
            "  가능한 원인:\n"
            "    1. prefix 가 1024 토큰보다 짧음 (OpenAI 임계값)\n"
            "    2. system_prompt 또는 첫 user 메시지에 가변 요소 포함 "
            "(timestamp/UUID/random)\n"
            "    3. dict 키 순서/JSON 직렬화 결과가 호출마다 다름\n"
            "    4. provider 가 prompt caching 을 지원하지 않거나 "
            "해당 모델에서 비활성화됨"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
