/**
 * TaskDraftPanel.tsx
 *
 * context_doc → Sonnet → 태스크 초안 생성 → 편집 → 파이프라인 시작
 *
 * 단계:
 *   generating → editing → saving → running | error
 */

import { useEffect, useReducer, useState } from 'react'
import { PipelineLogView, ACTIVE_JOB_KEY } from './PipelineLogView'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

// ── 타입 ──────────────────────────────────────────────────────────────────────

export interface DraftTask {
  id: string
  title: string
  description: string
  acceptance_criteria: string[]
  target_files: string[]
  depends_on: string[]
}

type Phase = 'generating' | 'editing' | 'saving' | 'running' | 'done' | 'error'

interface State {
  phase: Phase
  tasks: DraftTask[]
  errorMsg: string
  jobId: string
  rootDir: string    // 프로젝트 루트 = repo_path; tasks.yaml은 항상 rootDir/data/tasks.yaml
  agentCount: number
}

type Action =
  | { type: 'DRAFT_DONE'; tasks: DraftTask[] }
  | { type: 'ERROR'; msg: string }
  | { type: 'UPDATE_TASK'; idx: number; task: DraftTask }
  | { type: 'DELETE_TASK'; idx: number }
  | { type: 'ADD_TASK' }
  | { type: 'MOVE_TASK'; idx: number; dir: -1 | 1 }
  | { type: 'SET_ROOT'; path: string }
  | { type: 'SET_AGENT_COUNT'; count: number }
  | { type: 'SAVING' }
  | { type: 'RUNNING'; jobId: string }
  | { type: 'DONE' }

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'DRAFT_DONE':
      return { ...state, phase: 'editing', tasks: action.tasks }
    case 'ERROR':
      return { ...state, phase: 'error', errorMsg: action.msg }
    case 'UPDATE_TASK': {
      const tasks = [...state.tasks]
      tasks[action.idx] = action.task
      return { ...state, tasks }
    }
    case 'DELETE_TASK':
      return { ...state, tasks: state.tasks.filter((_, i) => i !== action.idx) }
    case 'ADD_TASK': {
      const nextNum = String(state.tasks.length + 1).padStart(3, '0')
      const newTask: DraftTask = {
        id: `task-${nextNum}`,
        title: '',
        description: '',
        acceptance_criteria: [''],
        target_files: [],
        depends_on: [],
      }
      return { ...state, tasks: [...state.tasks, newTask] }
    }
    case 'MOVE_TASK': {
      const tasks = [...state.tasks]
      const j = action.idx + action.dir
      if (j < 0 || j >= tasks.length) return state
      ;[tasks[action.idx], tasks[j]] = [tasks[j], tasks[action.idx]]
      return { ...state, tasks }
    }
    case 'SET_ROOT':
      return { ...state, rootDir: action.path }
    case 'SET_AGENT_COUNT':
      return { ...state, agentCount: Math.max(1, Math.min(8, action.count)) }
    case 'SAVING':
      return { ...state, phase: 'saving' }
    case 'RUNNING':
      return { ...state, phase: 'running', jobId: action.jobId }
    case 'DONE':
      return { ...state, phase: 'done' }
    default:
      return state
  }
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  contextDoc: string
  onBack: () => void
  onPipelineStarted?: (jobId: string) => void
}

// ── 컴포넌트 ──────────────────────────────────────────────────────────────────

export function TaskDraftPanel({ contextDoc, onBack, onPipelineStarted }: Props) {
  const [state, dispatch] = useReducer(reducer, {
    phase: 'generating',
    tasks: [],
    errorMsg: '',
    jobId: '',
    rootDir: '.',
    agentCount: 1,
  })

  // tasks.yaml은 항상 rootDir/data/tasks.yaml
  const tasksFilePath = state.rootDir === '.'
    ? 'data/tasks.yaml'
    : state.rootDir.replace(/\/+$/, '') + '/data/tasks.yaml'

  // 마운트 시 즉시 초안 생성 요청
  useEffect(() => {
    let cancelled = false
    async function generate() {
      try {
        const res = await fetch(`${API_BASE}/api/tasks/draft`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ context_doc: contextDoc }),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }))
          throw new Error(err.detail ?? '초안 생성 실패')
        }
        const data = await res.json()
        if (!cancelled) dispatch({ type: 'DRAFT_DONE', tasks: data.tasks ?? [] })
      } catch (e: unknown) {
        if (!cancelled) dispatch({ type: 'ERROR', msg: e instanceof Error ? e.message : String(e) })
      }
    }
    generate()
    return () => { cancelled = true }
  }, [contextDoc])


  const [browsing, setBrowsing] = useState(false)

  async function browseRoot() {
    setBrowsing(true)
    try {
      const initial = state.rootDir && state.rootDir !== '.' ? state.rootDir : '~'
      const res = await fetch(`${API_BASE}/api/utils/browse?type=folder&initial=${encodeURIComponent(initial)}`)
      const data = await res.json()
      if (!data.cancelled && data.path) dispatch({ type: 'SET_ROOT', path: data.path })
    } finally {
      setBrowsing(false)
    }
  }

  async function handleSaveAndRun() {
    dispatch({ type: 'SAVING' })
    try {
      // 1. tasks.yaml 저장 (rootDir/data/tasks.yaml)
      const saveRes = await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: state.tasks, tasks_path: tasksFilePath }),
      })
      if (!saveRes.ok) {
        const err = await saveRes.json().catch(() => ({ detail: saveRes.statusText }))
        throw new Error(err.detail ?? '저장 실패')
      }
      // 2. 파이프라인 실행
      const runRes = await fetch(`${API_BASE}/api/pipeline/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks_path: tasksFilePath,
          repo_path: state.rootDir === '.' ? '.' : state.rootDir,
          no_pr: false,
          max_workers: state.agentCount,
        }),
      })
      if (!runRes.ok) {
        const err = await runRes.json().catch(() => ({ detail: runRes.statusText }))
        throw new Error(err.detail ?? '파이프라인 시작 실패')
      }
      const runData = await runRes.json()
      localStorage.setItem(ACTIVE_JOB_KEY, runData.job_id)
      onPipelineStarted?.(runData.job_id)
      dispatch({ type: 'RUNNING', jobId: runData.job_id })
    } catch (e: unknown) {
      dispatch({ type: 'ERROR', msg: e instanceof Error ? e.message : String(e) })
    }
  }

  // ── 렌더 ────────────────────────────────────────────────────────────────────

  if (state.phase === 'generating') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-500 dark:text-zinc-400">Sonnet이 태스크를 생성하고 있습니다…</p>
      </div>
    )
  }

  if (state.phase === 'error') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <div className="text-3xl">❌</div>
        <p className="text-sm text-red-600 dark:text-red-400">{state.errorMsg}</p>
        <button
          className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800"
          onClick={onBack}
        >
          ← 돌아가기
        </button>
      </div>
    )
  }

  if (state.phase === 'running') {
    return <PipelineLogView jobId={state.jobId} onDone={() => dispatch({ type: 'DONE' })} />
  }

  if (state.phase === 'done') {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
        <div className="text-4xl">🚀</div>
        <h2 className="text-xl font-bold text-gray-800 dark:text-zinc-100">파이프라인 완료</h2>
        <p className="text-sm text-gray-500 dark:text-zinc-400">
          태스크가 실행되었습니다. PR과 리포트를 확인하세요.
        </p>
        <button
          className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800"
          onClick={onBack}
        >
          ← 회의 화면으로
        </button>
      </div>
    )
  }

  // editing | saving
  return (
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700">
        <div className="flex items-center gap-2">
          <button
            className="text-sm text-gray-500 dark:text-zinc-400 hover:text-gray-700 dark:hover:text-zinc-200"
            onClick={onBack}
          >
            ←
          </button>
          <span className="text-sm font-semibold text-gray-800 dark:text-zinc-100">
            태스크 초안 ({state.tasks.length}개)
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* 프로젝트 루트 (= repo_path, tasks는 rootDir/data/tasks.yaml 고정) */}
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-400 dark:text-zinc-500 shrink-0">프로젝트 루트</span>
            <div className="flex flex-col">
              <input
                className="text-xs rounded border border-gray-300 dark:border-zinc-600 px-2 py-1 bg-white dark:bg-zinc-800 text-gray-600 dark:text-zinc-300 w-52"
                value={state.rootDir === '.' ? '' : state.rootDir}
                onChange={e => dispatch({ type: 'SET_ROOT', path: e.target.value || '.' })}
                placeholder="/path/to/project"
                title="프로젝트 루트 디렉토리 (tasks.yaml은 여기/data/tasks.yaml에 저장됩니다)"
              />
              <span className="text-[10px] text-gray-400 dark:text-zinc-600 px-0.5 mt-0.5 truncate w-52">
                → {tasksFilePath}
              </span>
            </div>
            <button
              onClick={browseRoot}
              disabled={browsing}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 disabled:opacity-40 px-1 py-1 rounded transition-colors"
              title="파인더에서 프로젝트 루트 선택"
            >
              {browsing ? (
                <span className="inline-block w-3.5 h-3.5 border border-gray-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
                  <path d="M2 6a2 2 0 012-2h4l2 2h6a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
                </svg>
              )}
            </button>
          </div>
          {/* 에이전트 수 */}
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-400 dark:text-zinc-500 shrink-0">에이전트</span>
            <input
              type="number"
              min={1}
              max={8}
              className="text-xs rounded border border-gray-300 dark:border-zinc-600 px-2 py-1 bg-white dark:bg-zinc-800 text-gray-600 dark:text-zinc-300 w-12 text-center"
              value={state.agentCount}
              onChange={e => dispatch({ type: 'SET_AGENT_COUNT', count: parseInt(e.target.value) || 1 })}
              title="병렬 에이전트 수 (1~8)"
            />
          </div>
          <button
            className="rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            onClick={handleSaveAndRun}
            disabled={state.phase === 'saving' || state.tasks.length === 0}
          >
            {state.phase === 'saving' ? '저장 중…' : '저장 & 파이프라인 시작 🚀'}
          </button>
        </div>
      </div>

      {/* 태스크 목록 */}
      <div className="flex-1 overflow-auto p-4 space-y-3">
        {state.tasks.map((task, idx) => (
          <TaskCard
            key={task.id}
            task={task}
            idx={idx}
            total={state.tasks.length}
            onUpdate={t => dispatch({ type: 'UPDATE_TASK', idx, task: t })}
            onDelete={() => dispatch({ type: 'DELETE_TASK', idx })}
            onMove={dir => dispatch({ type: 'MOVE_TASK', idx, dir })}
          />
        ))}

        <button
          className="w-full rounded-lg border border-dashed border-gray-300 dark:border-zinc-600 py-3 text-sm text-gray-400 dark:text-zinc-500 hover:border-blue-400 hover:text-blue-500 transition-colors"
          onClick={() => dispatch({ type: 'ADD_TASK' })}
        >
          + 태스크 추가
        </button>
      </div>
    </div>
  )
}

// ── TaskCard ──────────────────────────────────────────────────────────────────

interface CardProps {
  task: DraftTask
  idx: number
  total: number
  onUpdate: (t: DraftTask) => void
  onDelete: () => void
  onMove: (dir: -1 | 1) => void
}

function TaskCard({ task, idx, total, onUpdate, onDelete, onMove }: CardProps) {
  function updateField<K extends keyof DraftTask>(key: K, value: DraftTask[K]) {
    onUpdate({ ...task, [key]: value })
  }

  function updateCriteria(i: number, val: string) {
    const next = [...task.acceptance_criteria]
    next[i] = val
    updateField('acceptance_criteria', next)
  }

  function addCriteria() {
    updateField('acceptance_criteria', [...task.acceptance_criteria, ''])
  }

  function removeCriteria(i: number) {
    updateField('acceptance_criteria', task.acceptance_criteria.filter((_, j) => j !== i))
  }

  return (
    <div className="rounded-xl border border-gray-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 space-y-3">
      {/* 태스크 헤더 */}
      <div className="flex items-start gap-2">
        <span className="text-xs font-mono text-gray-400 dark:text-zinc-500 pt-1 shrink-0">{task.id}</span>
        <input
          className="flex-1 text-sm font-semibold bg-transparent border-b border-transparent hover:border-gray-300 dark:hover:border-zinc-600 focus:border-blue-400 outline-none text-gray-800 dark:text-zinc-100 pb-0.5"
          value={task.title}
          onChange={e => updateField('title', e.target.value)}
          placeholder="태스크 제목"
        />
        <div className="flex items-center gap-1 shrink-0">
          <button
            className="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 disabled:opacity-30"
            onClick={() => onMove(-1)}
            disabled={idx === 0}
            title="위로"
          >↑</button>
          <button
            className="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 disabled:opacity-30"
            onClick={() => onMove(1)}
            disabled={idx === total - 1}
            title="아래로"
          >↓</button>
          <button
            className="w-6 h-6 flex items-center justify-center rounded text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
            onClick={onDelete}
            title="삭제"
          >✕</button>
        </div>
      </div>

      {/* 설명 */}
      <textarea
        className="w-full text-xs text-gray-600 dark:text-zinc-400 bg-gray-50 dark:bg-zinc-800 rounded-lg p-2 border border-gray-200 dark:border-zinc-700 focus:border-blue-400 outline-none resize-none"
        rows={2}
        value={task.description}
        onChange={e => updateField('description', e.target.value)}
        placeholder="설명"
      />

      {/* 수락 기준 */}
      <div>
        <p className="text-xs font-medium text-gray-500 dark:text-zinc-400 mb-1">수락 기준</p>
        <div className="space-y-1">
          {task.acceptance_criteria.map((c, i) => (
            <div key={i} className="flex gap-1">
              <span className="text-xs text-gray-400 dark:text-zinc-500 pt-1.5 shrink-0">{i + 1}.</span>
              <input
                className="flex-1 text-xs bg-gray-50 dark:bg-zinc-800 rounded px-2 py-1 border border-gray-200 dark:border-zinc-700 focus:border-blue-400 outline-none text-gray-700 dark:text-zinc-300"
                value={c}
                onChange={e => updateCriteria(i, e.target.value)}
                placeholder="테스트 가능한 조건"
              />
              <button
                className="text-xs text-gray-400 hover:text-red-500 px-1"
                onClick={() => removeCriteria(i)}
              >✕</button>
            </div>
          ))}
          <button
            className="text-xs text-blue-500 hover:text-blue-700 mt-0.5"
            onClick={addCriteria}
          >+ 조건 추가</button>
        </div>
      </div>

      {/* depends_on */}
      {task.depends_on.length > 0 && (
        <p className="text-xs text-gray-400 dark:text-zinc-500">
          선행 태스크: {task.depends_on.join(', ')}
        </p>
      )}
    </div>
  )
}

