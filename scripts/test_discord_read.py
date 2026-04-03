"""
Discord 채널 메시지 읽기 진단 스크립트.

사용법:
    python scripts/test_discord_read.py <channel_id>

이 스크립트는:
1. Discord API로 최근 10개 메시지를 읽는다
2. 각 메시지의 작성자(봇/사용자), 내용, ID를 출력한다
3. 5초 간격으로 새 메시지를 폴링하며 수신되는 메시지를 실시간 출력한다

Discord에서 "테스트"를 입력한 뒤 이 스크립트 출력에 나타나는지 확인하면
리스너 스레드 문제의 근본 원인을 알 수 있다.
"""
import os
import sys
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

DISCORD_API = "https://discord.com/api/v10"
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")

if not TOKEN:
    print("ERROR: DISCORD_BOT_TOKEN 환경변수가 없습니다")
    sys.exit(1)

channel_id = sys.argv[1] if len(sys.argv) > 1 else None
if not channel_id:
    # 채널 목록 출력
    print(f"채널 ID가 지정되지 않았습니다. 길드 {GUILD_ID}의 채널 목록을 조회합니다...\n")
    url = f"{DISCORD_API}/guilds/{GUILD_ID}/channels"
    headers = {"Authorization": f"Bot {TOKEN}"}
    resp = httpx.get(url, headers=headers, timeout=10)
    if not resp.is_success:
        print(f"ERROR: 채널 목록 조회 실패 (status={resp.status_code})")
        print(resp.text)
        sys.exit(1)
    channels = [c for c in resp.json() if c.get("type") == 0]
    for c in channels:
        print(f"  #{c['name']:30s}  id={c['id']}")
    print(f"\n사용법: python scripts/test_discord_read.py <channel_id>")
    sys.exit(0)

headers = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
}
url = f"{DISCORD_API}/channels/{channel_id}/messages"

print(f"=== Discord 메시지 읽기 테스트 ===")
print(f"채널 ID: {channel_id}")
print(f"토큰: {TOKEN[:10]}...{TOKEN[-4:]}")
print()

# 1단계: 최근 메시지 읽기
print("--- 최근 10개 메시지 ---")
resp = httpx.get(url, headers=headers, params={"limit": 10}, timeout=10)
if not resp.is_success:
    print(f"ERROR: 메시지 조회 실패 (status={resp.status_code})")
    print(resp.text)
    sys.exit(1)

messages = resp.json()
messages.sort(key=lambda m: int(m["id"]))
last_id = None
for m in messages:
    author = m.get("author", {})
    is_bot = author.get("bot", False)
    tag = "BOT" if is_bot else "USER"
    name = author.get("username", "?")
    content = m.get("content", "")[:80]
    print(f"  [{tag}] {name}: {content!r}  (id={m['id']})")
    last_id = m["id"]

print()
print("--- 실시간 폴링 시작 (Ctrl+C로 종료) ---")
print("Discord에 메시지를 입력해보세요. 여기에 나타나면 API는 정상입니다.\n")

try:
    poll_count = 0
    while True:
        time.sleep(2)
        poll_count += 1
        params = {"limit": 10}
        if last_id:
            params["after"] = last_id
        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=10)
            if not resp.is_success:
                print(f"  [poll #{poll_count}] API 오류: status={resp.status_code}")
                continue
            new_msgs = resp.json()
            if new_msgs:
                last_id = str(max(int(m["id"]) for m in new_msgs))
                for m in sorted(new_msgs, key=lambda x: int(x["id"])):
                    author = m.get("author", {})
                    is_bot = author.get("bot", False)
                    tag = "BOT" if is_bot else "USER"
                    name = author.get("username", "?")
                    content = m.get("content", "")[:80]
                    print(f"  >>> [{tag}] {name}: {content!r}  (id={m['id']})")
            elif poll_count % 15 == 0:  # 30초마다 heartbeat
                print(f"  [poll #{poll_count}] 대기 중... (last_id={last_id})")
        except Exception as e:
            print(f"  [poll #{poll_count}] 예외: {e}")
except KeyboardInterrupt:
    print("\n종료.")
