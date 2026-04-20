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

    # ── Pre-check: collect-only (task-025 guard) ────────────────────────────
    # fixture 초기화 없이 import/discovery 만 수행.
    # exit 70 = import/syntax 오류, exit 71 = 0개 수집.
    _collect_out=/tmp/collect.out
    if [ -n "${TEST_FILES:-}" ]; then
        # shellcheck disable=SC2086
        python -m pytest --collect-only -q $TEST_FILES > "$_collect_out" 2>&1
    else
        python -m pytest --collect-only -q > "$_collect_out" 2>&1
    fi
    _collect_rc=$?
    # exit 5 = pytest native "no tests collected" → 71 로 정규화
    if [ "$_collect_rc" -ne 0 ] && [ "$_collect_rc" -ne 5 ]; then
        echo "---COLLECTION_ERROR---"
        cat "$_collect_out"
        exit 70
    fi
    # pytest 출력: "N tests collected" / "collected N items" / "no tests collected" 등
    # 주의: 정규식 미매칭을 "0개" 로 오판하면 버전 변경 시 정상 태스크가 차단된다.
    # 파싱 실패(빈 문자열)와 "0 명시 매칭" 을 구분한다.
    _count=$(grep -Eo '([0-9]+) (tests?|items?)' "$_collect_out" \
             | awk '{print $1}' | tail -n 1)
    if [ "$_collect_rc" -eq 5 ]; then
        # pytest native "no tests collected" — 명시적 시그널.
        echo "---NO_TESTS_COLLECTED---"
        cat "$_collect_out"
        exit 71
    fi
    if [ -n "$_count" ] && [ "$_count" = "0" ]; then
        # 파서가 '0' 을 명시적으로 매칭한 경우에만 NO_TESTS_COLLECTED.
        echo "---NO_TESTS_COLLECTED---"
        cat "$_collect_out"
        exit 71
    fi
    if [ -z "$_count" ]; then
        # 파싱 실패: pytest 버전/플러그인 차이로 포맷이 달라졌을 수 있다.
        # 정상 수집됐는데 정규식만 못 잡았을 가능성이 높으므로 게이트 통과시키고
        # 진짜 실행에 맡긴다. 수집 단계 rc 가 0 이었으므로 안전한 기본값.
        echo "---COLLECTED: unknown (parser could not extract count, proceeding)---"
    else
        echo "---COLLECTED: ${_count}---"
    fi

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
    # --passWithNoTests=false: 매칭되는 테스트가 없으면 exit 1 + "No tests found" 출력.
    # runner.py 가 stdout 에서 "No tests found" 를 보고 [NO_TESTS_COLLECTED] 태깅.
    exec jest --no-coverage --forceExit --passWithNoTests=false 2>&1
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
    # ── Pre-check: go test -list 는 컴파일은 하지만 테스트는 실행하지 않는다.
    # 0개면 exit 71, 빌드 실패면 exit 70.
    _list_out=/tmp/go_list.out
    go test -list '.*' ./... > "$_list_out" 2>&1
    _list_rc=$?
    # Test/Example/Benchmark/Fuzz 함수 한 줄씩 나오는지 카운트
    # (Go 1.18+ 의 `Fuzz*` 함수도 실제 테스트 target 이므로 포함)
    _test_count=$(grep -Ec '^(Test|Example|Benchmark|Fuzz)[A-Z_0-9]' "$_list_out" 2>/dev/null || echo 0)
    if [ "$_list_rc" -ne 0 ] && [ "${_test_count:-0}" = "0" ]; then
        echo "---COLLECTION_ERROR---"
        cat "$_list_out"
        exit 70
    fi
    if [ "${_test_count:-0}" = "0" ]; then
        echo "---NO_TESTS_COLLECTED---"
        cat "$_list_out"
        exit 71
    fi
    echo "---COLLECTED: ${_test_count}---"
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
