/**
 * PipelineTaskTracker.tsx
 *
 * SSE 스트림을 파싱해 태스크별 5단계 파이프라인 상태를 시각화한다.
 * PipelineLogView와 같은 jobId를 받아 병렬로 동작할 수 있다.
 *
 * 표시 정보:
 *  - 태스크 헤더: 제목 · 상태 배지 · 경과 시간 · PR URL
 *  - 5단계 스텝 인디케이터: TestWriter → Implementer → TestRunner → Reviewer → Git
 *  - 활성 스텝: 최근 ReAct iteration (도구 호출 목록) 실시간 표시
 */

import { useEffect, useState } from 'react'
import type { LogEntry } from './PipelineLogView'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

// ── 타입 ─────────────────────────────────────────────────────────────────────

type StepStatus = 'idle' | 'active' | 'done' | 'failed' | 'retrying'
type PipelineStage = 'test_writer' | 'implementer' | 'test_runner' | 'reviewer' | 'git'
type TaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

interface ToolCallSnapshot {
  name: string
  input_preview: string
}

interface IterationSnapshot {
  iteration: number
  stage: PipelineStage
  toolCalls: ToolCallSnapshot[]
  elapsedMs: number
}

interface TaskState {
  id: string
  title: string
  status: TaskStatus
  activeStage: PipelineStage | null
  stages: Record<PipelineStage, StepStatus>
  iterations: IterationSnapshot[]
  elapsed?: number
  prUrl?: string
  failReason?: string
}

type PipelineSummary = {
  total: number
  success: number
  fail: number
  ended: boolean
}

// ── 상수 & 매핑 ───────────────────────────────────────────────────────────────

const STAGE_LABELS: Record<PipelineStage, string> = {
  test_writer: 'TestWriter',
  implementer: 'Implementer',
  test_runner: 'TestRunner',
  reviewer: 'Reviewer',
  git: 'Git',
}

const STAGE_ORDER: PipelineStage[] = [
  'test_writer',
  'implementer',
  'test_runner',
  'reviewer',
  'git',
]

// step 이벤트의 step 값 → PipelineStage + 해당 스텝 이후 상태 변화 여부
const STEP_MAP: Record<string, { stage: PipelineStage; effect: 'activate' | 'complete' | 'fail' | 'retry' }> = {
  test_writing:       { stage: 'test_writer',  effect: 'activate'  },
  test_writing_retry: { stage: 'test_writer',  effect: 'retry'     },
  quality_gate:       { stage: 'test_writer',  effect: 'activate'  },
  test_written:       { stage: 'test_writer',  effect: 'activate'  },
  quality_gate_ok:    { stage: 'test_writer',  effect: 'complete'  },
  implementing:       { stage: 'implementer',  effect: 'activate'  },
  docker_running:     { stage: 'test_runner',  effect: 'activate'  },
  docker_pass:        { stage: 'test_runner',  effect: 'complete'  },
  docker_fail:        { stage: 'test_runner',  effect: 'fail'      },
  reviewing:          { stage: 'reviewer',     effect: 'activate'  },
  review_approved:    { stage: 'reviewer',     effect: 'complete'  },
  review_rejected:    { stage: 'reviewer',     effect: 'fail'      },
  review_retry:       { stage: 'implementer',  effect: 'retry'     },
  git:                { stage: 'git',          effect: 'activate'  },
  paused:             { stage: 'git',          effect: 'activate'  }, // no-op visual
  resumed:            { stage: 'git',          effect: 'activate'  }, // no-op visual
}

function makeInitialStages(): Record<PipelineStage, StepStatus> {
  return {
    test_writer: 'idle',
    implementer: 'idle',
    test_runner: 'idle',
    reviewer: 'idle',
    git: 'idle',
  }
}

// ── 이벤트 → 태스크 상태 리듀서 ──────────────────────────────────────────────

function reduceEvent(
  tasks: Map<string, TaskState>,
  event: LogEntry & { task_id?: string; iteration?: number; tool_calls?: ToolCallSnapshot[]; elapsed_ms?: number },
): Map<string, TaskState> {
  const next = new Map(tasks)

  const getOrCreate = (id: string, title?: string): TaskState => {
    if (!next.has(id)) {
      next.set(id, {
        id,
        title: title ?? id,
        status: 'pending',
        activeStage: null,
        stages: makeInitialStages(),
        iterations: [],
      })
    }
    return { ...next.get(id)! }
  }

  switch (event.type) {
    case 'task_start': {
      const t = getOrCreate(event.task_id!, event.title)
      t.status = 'running'
      next.set(t.id, t)
      break
    }

    case 'step': {
      if (!event.task_id) break
      const mapping = STEP_MAP[event.step ?? '']
      if (!mapping) break
      const t = getOrCreate(event.task_id)
      const stages = { ...t.stages }
      const { stage, effect } = mapping

      if (effect === 'activate') {
        // 이전 활성 스텝이 다른 스테이지면 complete로 전환
        if (t.activeStage && t.activeStage !== stage && stages[t.activeStage] === 'active') {
          stages[t.activeStage] = 'done'
        }
        stages[stage] = 'active'
        t.activeStage = stage
      } else if (effect === 'complete') {
        stages[stage] = 'done'
        t.activeStage = null
      } else if (effect === 'fail') {
        stages[stage] = 'failed'
        t.activeStage = null
      } else if (effect === 'retry') {
        // docker_fail 후 다시 implementer로: implementer를 active로, test_runner 리셋
        stages.test_runner = stages.test_runner === 'failed' ? 'idle' : stages.test_runner
        stages[stage] = 'active'
        t.activeStage = stage
      }
      t.stages = stages
      next.set(t.id, t)
      break
    }

    case 'agent_iteration': {
      if (!event.task_id) break
      const t = getOrCreate(event.task_id)
      const snap: IterationSnapshot = {
        iteration: event.iteration ?? 0,
        stage: t.activeStage ?? 'implementer',
        toolCalls: event.tool_calls ?? [],
        elapsedMs: event.elapsed_ms ?? 0,
      }
      // 최근 10개만 유지
      t.iterations = [...t.iterations, snap].slice(-10)
      next.set(t.id, t)
      break
    }

    case 'task_done': {
      if (!event.task_id) break
      const t = getOrCreate(event.task_id, event.title)
      t.status = 'done'
      t.elapsed = event.elapsed
      t.prUrl = event.pr_url
      t.activeStage = null
      // git 완료
      t.stages = { ...t.stages, git: 'done' }
      next.set(t.id, t)
      break
    }

    case 'task_fail': {
      if (!event.task_id) break
      const t = getOrCreate(event.task_id, event.title)
      t.status = 'failed'
      t.elapsed = event.elapsed
      t.failReason = event.reason
      if (t.activeStage) {
        t.stages = { ...t.stages, [t.activeStage]: 'failed' }
        t.activeStage = null
      }
      next.set(t.id, t)
      break
    }

    case 'task_skip': {
      if (!event.task_id) break
      const t = getOrCreate(event.task_id, event.title)
      t.status = 'skipped'
      next.set(t.id, t)
      break
    }
  }

  return next
}

// ── 스텝 인디케이터 컴포넌트 ──────────────────────────────────────────────────

function StageIndicator({ stage, status, isLast }: { stage: PipelineStage; status: StepStatus; isLast: boolean }) {
  const base = 'relative flex items-center'

  const dotClass = (() => {
    switch (status) {
      case 'active':    return 'w-3 h-3 rounded-full bg-blue-500 animate-pulse ring-2 ring-blue-400/40'
      case 'done':      return 'w-3 h-3 rounded-full bg-emerald-500'
      case 'failed':    return 'w-3 h-3 rounded-full bg-red-500'
      case 'retrying':  return 'w-3 h-3 rounded-full bg-amber-400 animate-pulse'
      default:          return 'w-3 h-3 rounded-full bg-zinc-600'
    }
  })()

  const labelClass = (() => {
    switch (status) {
      case 'active':   return 'text-blue-400 font-semibold'
      case 'done':     return 'text-emerald-400'
      case 'failed':   return 'text-red-400'
      case 'retrying': return 'text-amber-400'
      default:         return 'text-zinc-500'
    }
  })()

  return (
    <div className={base}>
      <div className="flex flex-col items-center gap-1">
        <div className={dotClass} />
        <span className={`text-[10px] leading-tight ${labelClass} whitespace-nowrap`}>
          {STAGE_LABELS[stage]}
        </span>
      </div>
      {!isLast && (
        <div className={`h-px w-6 sm:w-10 mx-1 mt-[-10px] ${
          status === 'done' ? 'bg-emerald-600' : 'bg-zinc-700'
        }`} />
      )}
    </div>
  )
}

// ── 단일 태스크 카드 ──────────────────────────────────────────────────────────

function TaskCard({ task }: { task: TaskState }) {
  const [expanded, setExpanded] = useState(false)
  const isActive = task.status === 'running'

  // 활성 태스크는 자동으로 펼침
  useEffect(() => {
    if (isActive) setExpanded(true)
  }, [isActive])

  const statusBadge = (() => {
    switch (task.status) {
      case 'running':  return <span className="px-1.5 py-0.5 rounded text-[10px] bg-blue-900/60 text-blue-300 animate-pulse">실행 중</span>
      case 'done':     return <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-900/60 text-emerald-300">완료</span>
      case 'failed':   return <span className="px-1.5 py-0.5 rounded text-[10px] bg-red-900/60 text-red-300">실패</span>
      case 'skipped':  return <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-400">건너뜀</span>
      default:         return <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-500">대기</span>
    }
  })()

  const activeIterations = task.iterations.filter(it => it.stage === task.activeStage).slice(-3)

  return (
    <div className={`rounded-lg border transition-colors ${
      isActive
        ? 'border-blue-700/60 bg-zinc-900'
        : task.status === 'done'
        ? 'border-emerald-900/40 bg-zinc-900/60'
        : task.status === 'failed'
        ? 'border-red-900/40 bg-zinc-900/60'
        : 'border-zinc-800 bg-zinc-900/40'
    }`}>
      {/* 카드 헤더 */}
      <button
        className="w-full flex items-center justify-between px-3 py-2.5 text-left gap-2"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono text-zinc-500 shrink-0">{task.id}</span>
          <span className="text-xs text-zinc-200 truncate">{task.title}</span>
          {statusBadge}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {task.elapsed !== undefined && (
            <span className="text-[10px] text-zinc-500">{task.elapsed.toFixed(0)}s</span>
          )}
          {task.prUrl && (
            <a
              href={task.prUrl}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              className="text-[10px] text-blue-400 hover:underline"
            >
              PR →
            </a>
          )}
          <span className="text-zinc-600 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* 스텝 인디케이터 */}
      <div className="px-3 pb-2.5 flex items-start gap-0">
        {STAGE_ORDER.map((stage, idx) => (
          <StageIndicator
            key={stage}
            stage={stage}
            status={task.stages[stage]}
            isLast={idx === STAGE_ORDER.length - 1}
          />
        ))}
      </div>

      {/* 펼쳐진 상세 뷰 */}
      {expanded && (
        <div className="border-t border-zinc-800 px-3 py-2 space-y-1.5">
          {/* 실패 원인 */}
          {task.failReason && (
            <p className="text-[11px] text-red-400 leading-snug">
              실패: {task.failReason.replace(/^\[MAX_ITER\]\s*/, '').slice(0, 200)}
            </p>
          )}

          {/* 현재 활성 스텝의 최근 iterations */}
          {isActive && activeIterations.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] text-zinc-500 uppercase tracking-wide">
                {STAGE_LABELS[task.activeStage!]} — 최근 반복
              </p>
              {activeIterations.map((it, i) => (
                <div key={i} className="text-[11px] text-zinc-400 leading-snug">
                  <span className="text-zinc-500">#{it.iteration}</span>
                  {' '}
                  {it.toolCalls.length === 0
                    ? <span className="text-zinc-600">도구 없음</span>
                    : it.toolCalls.map((tc, j) => (
                        <span key={j} className="inline-block mr-1.5">
                          <span className="text-sky-400">{tc.name}</span>
                          <span className="text-zinc-600 ml-0.5 text-[10px]">
                            {summarizeInput(tc.input_preview)}
                          </span>
                        </span>
                      ))
                  }
                  <span className="text-zinc-600 ml-1">({it.elapsedMs.toFixed(0)}ms)</span>
                </div>
              ))}
            </div>
          )}

          {/* 이전 모든 iterations 요약 */}
          {task.iterations.length > 0 && (
            <p className="text-[10px] text-zinc-600">
              총 {task.iterations.length}회 반복 ·{' '}
              도구 {task.iterations.reduce((s, it) => s + it.toolCalls.length, 0)}건 호출
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** 도구 input 미리보기에서 핵심 정보만 추출 */
function summarizeInput(preview: string | undefined): string {
  if (!preview) return ''
  // path='...' or "path": "..." 패턴 추출
  const pathMatch = preview.match(/['"](path|file_path)['"]\s*[:=]\s*['"]([^'"]{1,50})['"]/)
  if (pathMatch) {
    const p = pathMatch[2]
    const parts = p.split('/')
    return `(${parts[parts.length - 1]})`
  }
  // command='...' 패턴
  const cmdMatch = preview.match(/['"](command)['"]\s*[:=]\s*['"]([^'"]{1,40})['"]/)
  if (cmdMatch) return `(${cmdMatch[2]})`
  return ''
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────

interface Props {
  jobId: string
  /** SSE end/pipeline_aborted 이벤트 수신 직후 호출 — 사이드바 배지 제거용 */
  onEnded?: () => void
}

export function PipelineTaskTracker({ jobId, onEnded }: Props) {
  const [tasks, setTasks] = useState<Map<string, TaskState>>(new Map())
  const [summary, setSummary] = useState<PipelineSummary>({ total: 0, success: 0, fail: 0, ended: false })
  const [taskOrder, setTaskOrder] = useState<string[]>([])

  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/pipeline/stream/${jobId}`)

    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data) as LogEntry & {
          task_id?: string
          iteration?: number
          tool_calls?: ToolCallSnapshot[]
          elapsed_ms?: number
        }

        if (event.type === 'pipeline_start') {
          setSummary(s => ({ ...s, total: event.total ?? 0 }))
          return
        }
        if (event.type === 'task_start' && event.task_id) {
          setTaskOrder(prev => prev.includes(event.task_id!) ? prev : [...prev, event.task_id!])
        }
        if (event.type === 'pipeline_done') {
          setSummary(s => ({ ...s, success: event.success ?? 0, fail: event.fail ?? 0, ended: true }))
        }
        if (event.type === 'end' || event.type === 'pipeline_aborted') {
          setSummary(s => ({ ...s, ended: true }))
          onEnded?.()
          es.close()
          return
        }

        setTasks(prev => reduceEvent(prev, event))
      } catch { /* 파싱 오류 무시 */ }
    }

    es.onerror = () => { es.close() }

    return () => es.close()
  }, [jobId])

  const orderedTasks = taskOrder
    .map(id => tasks.get(id))
    .filter((t): t is TaskState => !!t)

  // 대기 중인 태스크(아직 task_start 못 받은 것)는 없으므로 여기선 신경 안 써도 됨

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 헤더 */}
      <div className="px-4 py-2.5 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2">
          {!summary.ended ? (
            <div className="w-2.5 h-2.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          ) : (
            <div className={`w-2.5 h-2.5 rounded-full ${summary.fail > 0 ? 'bg-amber-400' : 'bg-emerald-500'}`} />
          )}
          <span className="text-sm font-semibold text-gray-800 dark:text-zinc-100">
            {summary.ended ? '파이프라인 완료' : '파이프라인 실행 중…'}
          </span>
          {summary.ended && (
            <span className="text-xs text-zinc-500">
              성공 {summary.success} · 실패 {summary.fail}
            </span>
          )}
        </div>
        <span className="text-xs font-mono text-zinc-500">{jobId.slice(0, 8)}</span>
      </div>

      {/* 태스크 카드 목록 */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-zinc-950">
        {orderedTasks.length === 0 && (
          <div className="text-xs text-zinc-600 animate-pulse pt-2">태스크 대기 중…</div>
        )}
        {orderedTasks.map(task => (
          <TaskCard key={task.id} task={task} />
        ))}
      </div>
    </div>
  )
}
