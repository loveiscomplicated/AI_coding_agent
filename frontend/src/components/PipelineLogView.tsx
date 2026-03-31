/**
 * PipelineLogView.tsx
 *
 * SSE 스트림을 구독하여 파이프라인 실시간 로그를 표시한다.
 * jobId를 localStorage에 저장해 페이지 새로고침 후 재연결을 지원한다.
 */

import { useEffect, useRef, useState } from 'react'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string
export const ACTIVE_JOB_KEY = 'pipeline_active_job_id'

// ── 타입 ─────────────────────────────────────────────────────────────────────

export interface LogEntry {
  type: string
  task_id?: string
  title?: string
  step?: string
  message?: string
  reason?: string
  pr_url?: string
  elapsed?: number
  timestamp?: string
  success?: number
  fail?: number
  total?: number
  status?: string
  next_task_id?: string
  // merge fields
  base_branch?: string
  branch?: string
  branches?: string[]
  count?: number
  merged_count?: number
  conflicts_resolved?: number
  error?: string
  summary?: string
  // orchestrator fields
  attempt?: number
  next_attempt?: number
  max_attempts?: number
  hint?: string
  failure_reason?: string
  is_max_iter?: boolean
  total_attempts?: number
  report?: string
  report_path?: string
}

// ── 로그 텍스트 포맷 ──────────────────────────────────────────────────────────

function logEntryText(e: LogEntry): { text: string; color: string } {
  switch (e.type) {
    case 'pipeline_start':
      return { text: `🚀 파이프라인 시작 — ${e.total ?? 0}개 태스크`, color: 'text-blue-400' }
    case 'task_start':
      return { text: `▶ [${e.task_id}] ${e.title}`, color: 'text-cyan-400' }
    case 'step':
      return { text: `  · ${e.message}`, color: 'text-zinc-500' }
    case 'test_pass':
      return { text: `  ✓ ${e.message}`, color: 'text-green-400' }
    case 'task_done':
      return {
        text: `✅ [${e.task_id}] ${e.title} 완료 (${e.elapsed}s)${e.pr_url ? `  PR → ${e.pr_url}` : ''}`,
        color: 'text-green-400',
      }
    case 'task_fail':
      return {
        text: `❌ [${e.task_id}] ${e.title} 실패${e.is_max_iter ? ' [반복 초과]' : ''} — ${e.reason}`,
        color: 'text-red-400',
      }
    case 'task_skip':
      return { text: `⊘ [${e.task_id}] ${e.title} 건너뜀 — ${e.reason}`, color: 'text-zinc-500' }
    case 'pipeline_done':
      return {
        text: `🏁 완료 — 성공 ${e.success ?? 0}  실패 ${e.fail ?? 0}`,
        color: (e.fail ?? 0) > 0 ? 'text-amber-400' : 'text-green-400',
      }
    case 'paused':
      return { text: `⏸ 일시정지 — '계속' 입력 시 ${e.next_task_id ?? '다음 태스크'}부터 재개`, color: 'text-amber-400' }
    case 'resumed':
      return { text: `▶ 재개 — ${e.task_id ?? ''}`, color: 'text-cyan-400' }
    case 'pipeline_aborted':
      return { text: `🛑 ${e.message ?? '사용자 중단 요청으로 파이프라인 종료'}`, color: 'text-red-400' }
    case 'error':
      return { text: `⚠ ${e.message}`, color: 'text-red-400' }
    case 'end':
      return { text: '─── 스트림 종료 ───', color: 'text-zinc-600' }
    // ── 자동 머지 이벤트 ────────────────────────────────────────────────────
    case 'catchup_merge_start':
      return {
        text: `🔁 catch-up 머지 — 미머지 브랜치 ${e.count}개 순서대로 처리\n     ${(e.branches as string[] | undefined ?? []).join(' → ')}`,
        color: 'text-purple-400',
      }
    case 'merge_start':
      return { text: `🔀 자동 머지 시작 — ${e.base_branch} ← ${e.count}개 브랜치`, color: 'text-blue-400' }
    case 'merge_branch':
      return { text: `  · 머지 중: ${e.branch} → ${e.base_branch}`, color: 'text-zinc-500' }
    case 'merge_done':
      return {
        text: `  ✓ 머지 완료: ${e.branch}${(e.conflicts_resolved ?? 0) > 0 ? `  (충돌 ${e.conflicts_resolved}개 자동 해결)` : ''}`,
        color: 'text-green-400',
      }
    case 'merge_fail':
      return { text: `  ✗ 머지 실패: ${e.branch} — ${e.error}`, color: 'text-red-400' }
    case 'merge_testing':
      return { text: `  · 머지 후 테스트 실행 중…`, color: 'text-zinc-500' }
    case 'merge_test_pass':
      return { text: `  ✓ 머지 후 테스트 통과: ${e.summary}`, color: 'text-green-400' }
    case 'merge_test_fail':
      return { text: `  ✗ 머지 후 테스트 실패 — 머지 취소: ${e.summary}`, color: 'text-red-400' }
    case 'merge_pushed':
      return { text: `🚀 ${e.base_branch} push 완료 (${e.merged_count}개 머지)`, color: 'text-blue-400' }
    case 'merge_push_fail':
      return { text: `⚠ push 실패: ${e.error}`, color: 'text-amber-400' }
    // ── 오케스트레이터 개입 이벤트 ─────────────────────────────────────────
    case 'orchestrator_analyzing':
      return {
        text: `🔍 [${e.task_id}] 오케스트레이터 분석 중… (시도 ${e.attempt}/${e.max_attempts})${e.is_max_iter ? ' — 반복 초과' : ''}\n     원인: ${(e.failure_reason ?? '').replace(/^\[MAX_ITER\]\s*/, '').slice(0, 120)}`,
        color: 'text-amber-400',
      }
    case 'orchestrator_retry':
      return {
        text: `🔄 [${e.task_id}] 오케스트레이터 재시도 결정 (${e.attempt} → ${e.next_attempt}회차)\n     💡 힌트: ${(e.hint ?? '').slice(0, 200)}`,
        color: 'text-amber-300',
      }
    case 'orchestrator_giveup':
      return {
        text: `🛑 [${e.task_id}] 오케스트레이터 포기 — ${(e.reason ?? '').slice(0, 150)}`,
        color: 'text-red-400',
      }
    case 'orchestrator_report_generating':
      return {
        text: `📊 [${e.task_id}] 오케스트레이터 최종 실패 보고서 생성 중… (총 ${e.total_attempts}회 시도)`,
        color: 'text-orange-400',
      }
    case 'orchestrator_report':
      return {
        text: `📋 [${e.task_id}] 보고서 저장 완료 → ${e.report_path ?? ''}\n${(e.report ?? '').split('\n').slice(0, 6).map((l: string) => '     ' + l).join('\n')}`,
        color: 'text-orange-300',
      }
    default:
      return { text: JSON.stringify(e), color: 'text-zinc-600' }
  }
}

// ── 컴포넌트 ──────────────────────────────────────────────────────────────────

interface Props {
  jobId: string
  /** 파이프라인 종료(end 이벤트) 후 "결과 확인" 버튼 클릭 시 호출 */
  onDone: () => void
}

export function PipelineLogView({ jobId, onDone }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [ended, setEnded] = useState(false)
  const [disconnected, setDisconnected] = useState(false)
  const [paused, setPaused] = useState(false)
  const [controlling, setControlling] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // 새로고침 후 재연결을 위해 jobId를 배열에 추가 (기존 항목 보존)
    const raw = localStorage.getItem(ACTIVE_JOB_KEY)
    let ids: string[] = []
    try {
      const parsed = JSON.parse(raw ?? '[]')
      ids = Array.isArray(parsed) ? parsed : [String(parsed)]
    } catch {
      ids = raw ? [raw] : []
    }
    if (!ids.includes(jobId)) ids = [...ids, jobId]
    localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(ids))

    const es = new EventSource(`${API_BASE}/api/pipeline/stream/${jobId}`)

    es.onmessage = (ev) => {
      try {
        const event: LogEntry = JSON.parse(ev.data)
        setLogs(prev => [...prev, event])
        if (event.type === 'end') {
          setEnded(true)
          // 배열에서 이 jobId만 제거
          const r = localStorage.getItem(ACTIVE_JOB_KEY)
          try {
            const p = JSON.parse(r ?? '[]')
            const next = (Array.isArray(p) ? p : [String(p)]).filter((id: string) => id !== jobId)
            next.length > 0
              ? localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(next))
              : localStorage.removeItem(ACTIVE_JOB_KEY)
          } catch { localStorage.removeItem(ACTIVE_JOB_KEY) }
          es.close()
        }
        if (event.type === 'paused') setPaused(true)
        if (event.type === 'resumed') setPaused(false)
        if (event.type === 'pipeline_aborted') { setPaused(false); setEnded(true) }
      } catch { /* 파싱 오류 무시 */ }
    }

    es.onerror = () => {
      // 백엔드가 다운된 경우
      setDisconnected(true)
      setEnded(true)
      es.close()
      // localStorage는 유지 — 백엔드가 살아나면 재연결 가능
    }

    return () => es.close()
  }, [jobId])

  // 새 로그 추가 시 스크롤 하단 유지
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  const handleDone = () => {
    const r = localStorage.getItem(ACTIVE_JOB_KEY)
    try {
      const p = JSON.parse(r ?? '[]')
      const next = (Array.isArray(p) ? p : [String(p)]).filter((id: string) => id !== jobId)
      next.length > 0
        ? localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(next))
        : localStorage.removeItem(ACTIVE_JOB_KEY)
    } catch { localStorage.removeItem(ACTIVE_JOB_KEY) }
    onDone()
  }

  async function sendControl(action: 'pause' | 'resume' | 'stop') {
    setControlling(true)
    try {
      await fetch(`${API_BASE}/api/pipeline/control/${jobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
    } finally {
      setControlling(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 flex-shrink-0">
        <div className="flex items-center gap-2">
          {!ended && !paused ? (
            <div className="w-3 h-3 border-2 border-green-500 border-t-transparent rounded-full animate-spin" />
          ) : paused ? (
            <div className="w-3 h-3 rounded-full bg-amber-400" />
          ) : disconnected ? (
            <div className="w-3 h-3 rounded-full bg-amber-500" />
          ) : (
            <div className="w-3 h-3 rounded-full bg-green-500" />
          )}
          <span className="text-sm font-semibold text-gray-800 dark:text-zinc-100">
            {paused ? '⏸ 일시정지됨' : !ended ? '파이프라인 실행 중…' : disconnected ? '연결 끊김' : '파이프라인 완료'}
          </span>
          <span className="text-xs text-gray-400 dark:text-zinc-500 font-mono">{jobId.slice(0, 8)}</span>
        </div>
        <div className="flex items-center gap-2">
          {disconnected && (
            <span className="text-xs text-amber-500">
              백엔드가 재시작되면 대시보드에서 결과를 확인하세요
            </span>
          )}
          {/* 실행 중 제어 버튼 */}
          {!ended && (
            <div className="flex items-center gap-1">
              {paused ? (
                <button
                  onClick={() => sendControl('resume')}
                  disabled={controlling}
                  className="rounded px-2.5 py-1 text-xs font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 transition-colors"
                  title="파이프라인 재개"
                >
                  ▶ 계속
                </button>
              ) : (
                <button
                  onClick={() => sendControl('pause')}
                  disabled={controlling}
                  className="rounded px-2.5 py-1 text-xs font-medium bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
                  title="다음 태스크 전 일시정지"
                >
                  ⏸ 멈춤
                </button>
              )}
              <button
                onClick={() => { if (confirm('파이프라인을 중단하시겠습니까?')) sendControl('stop') }}
                disabled={controlling}
                className="rounded px-2.5 py-1 text-xs font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                title="파이프라인 중단"
              >
                ■ 중단
              </button>
            </div>
          )}
          {ended && (
            <button
              onClick={handleDone}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 transition-colors"
            >
              결과 확인 →
            </button>
          )}
        </div>
      </div>

      {/* 로그 */}
      <div className="flex-1 overflow-y-auto bg-zinc-950 p-4 font-mono">
        {logs.length === 0 && !ended && (
          <div className="text-xs text-zinc-600 animate-pulse">연결 중…</div>
        )}
        {logs.map((entry, i) => {
          const { text, color } = logEntryText(entry)
          return (
            <div key={i} className={`text-xs leading-5 ${color} whitespace-pre-wrap break-all`}>
              {text}
            </div>
          )
        })}
        {!ended && (
          <div className="text-xs text-zinc-600 mt-1 animate-pulse">▌</div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
