#!/bin/sh
# docker-entrypoint.sh
#
# TEST_FRAMEWORK 환경 변수로 테스트 실행 방식을 결정한다.
#
# 동작 규칙:
#   1. 알려진 shorthand(pytest, jest, vitest, go, rspec, minitest)는 preset 명령으로 처리.
#      (workspace copy, 의존성 설치 등을 자동으로 처리해 준다)
#   2. 그 외 모든 값은 shell 명령으로 그대로 실행된다.
#      예: "cargo test", "mvn test -q", "dotnet test", "phpunit tests/"
#   3. /workspace/setup.sh 가 있으면 테스트 실행 전에 먼저 실행된다.
#      (런타임 설치, 빌드 등 사전 작업에 활용)
#
# workspace는 read-only(:ro)로 마운트되므로, 쓰기가 필요한 경우 /tmp에 복사한다.

TEST_FRAMEWORK="${TEST_FRAMEWORK:-pytest}"

# setup.sh 실행 (존재하는 경우)
if [ -f /workspace/setup.sh ]; then
    sh /workspace/setup.sh
fi

# workspace를 /tmp에 복사 (쓰기가 필요한 경우에 사용)
_copy_workspace() {
    cp -r /workspace /tmp/ws
    echo "/tmp/ws"
}

case "$TEST_FRAMEWORK" in

  # ── Python / pytest ─────────────────────────────────────────────────────────
  pytest)
    if [ -f /workspace/requirements.txt ]; then
        pip install --no-cache-dir -q -r /workspace/requirements.txt
    fi
    # src/ 디렉토리를 PYTHONPATH에 추가하여 테스트에서 직접 import 가능하게 함
    # 예: tests/에서 `from eggDoneness import ...` → src/eggDoneness.py 를 찾음
    PYTHONPATH="/workspace/src:/workspace:${PYTHONPATH:-}"
    export PYTHONPATH
    # TEST_FILES가 지정되면 해당 파일만 실행, 아니면 전체 실행
    if [ -n "${TEST_FILES:-}" ]; then
        exec python -m pytest $TEST_FILES -v --tb=short 2>&1
    fi
    exec python -m pytest --tb=short -q --no-header "$@"
    ;;

  # ── JavaScript / Jest ───────────────────────────────────────────────────────
  jest)
    WS=$(_copy_workspace)
    cd "$WS"
    [ -f package.json ] && npm install --silent 2>/dev/null
    exec jest --no-coverage --forceExit 2>&1
    ;;

  # ── JavaScript / Vitest ─────────────────────────────────────────────────────
  vitest)
    WS=$(_copy_workspace)
    cd "$WS"
    [ -f package.json ] && npm install --silent 2>/dev/null
    exec npx vitest run --reporter=verbose 2>&1
    ;;

  # ── Go / go test ────────────────────────────────────────────────────────────
  go)
    cd /workspace
    exec go test ./... -v 2>&1
    ;;

  # ── Ruby / RSpec ─────────────────────────────────────────────────────────────
  rspec)
    WS=$(_copy_workspace)
    cd "$WS"
    [ -f Gemfile ] && bundle install --quiet 2>/dev/null
    exec bundle exec rspec --format progress 2>&1
    ;;

  # ── Ruby / Minitest ──────────────────────────────────────────────────────────
  minitest)
    WS=$(_copy_workspace)
    cd "$WS"
    [ -f Gemfile ] && bundle install --quiet 2>/dev/null
    exec ruby -Ilib -Itest test/**/*.rb 2>&1
    ;;

  # ── Kotlin / Java / Gradle ───────────────────────────────────────────────────
  gradle)
    WS=$(_copy_workspace)
    cd "$WS"
    if [ -f gradlew ]; then
        chmod +x gradlew
        exec ./gradlew test --console=plain 2>&1
    else
        exec gradle test --console=plain 2>&1
    fi
    ;;

  # ── C / make ─────────────────────────────────────────────────────────────────
  c)
    WS=$(_copy_workspace)
    cd "$WS"
    exec make test 2>&1
    ;;

  # ── C++ / cmake + ctest ───────────────────────────────────────────────────────
  cpp)
    WS=$(_copy_workspace)
    cd "$WS"
    mkdir -p _build
    cd _build
    cmake .. -DCMAKE_BUILD_TYPE=Debug 2>&1
    cmake --build . 2>&1
    exec ctest --output-on-failure 2>&1
    ;;

  # ── Python / 프레임워크 없는 방식 ─────────────────────────────────────────────
  # tests/test_*.py 를 직접 python으로 실행한다.
  # 각 파일은 성공 시 exit 0, 실패 시 exit 1 을 반환하는 규약을 따른다.
  python)
    if [ -f /workspace/requirements.txt ]; then
        pip install --no-cache-dir -q -r /workspace/requirements.txt
    fi
    PYTHONPATH="/workspace/src:/workspace:${PYTHONPATH:-}"
    export PYTHONPATH
    FAILED=0
    for f in /workspace/tests/test_*.py; do
        [ -f "$f" ] || continue
        python "$f" || FAILED=1
    done
    exit $FAILED
    ;;

  # ── JavaScript / 프레임워크 없는 방식 ─────────────────────────────────────────
  # tests/test_*.js 를 직접 node로 실행한다.
  # 각 파일은 성공 시 exit 0, 실패 시 exit 1 을 반환하는 규약을 따른다.
  node)
    WS=$(_copy_workspace)
    cd "$WS"
    FAILED=0
    for f in tests/test_*.js; do
        [ -f "$f" ] || continue
        node "$f" || FAILED=1
    done
    exit $FAILED
    ;;

  # ── 그 외: 값 자체를 shell 명령으로 실행 ─────────────────────────────────────
  # 예: "cargo test", "mvn test -q", "dotnet test", "phpunit tests/", "swift test"
  *)
    WS=$(_copy_workspace)
    cd "$WS"
    eval "$TEST_FRAMEWORK"
    ;;

esac
