/**
 * TaskDraftPanel.tsx
 *
 * context_doc → Sonnet → 태스크 초안 생성 → 편집 → 파이프라인 시작
 *
 * 단계:
 *   generating → editing → saving → running | error
 */

import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { PipelineLogView, ACTIVE_JOB_KEY } from './PipelineLogView'
import { AvailableModel, PipelineModelModal } from './PipelineModelModal'
import { DependencyGraphModal } from './DependencyGraphModal'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

// ── 타입 ──────────────────────────────────────────────────────────────────────

interface CritiqueIssue {
  task_id: string
  severity: 'ERROR' | 'WARNING'
  category: 'sizing' | 'testability' | 'dependency' | 'scope' | 'description'
  message: string
}

interface CritiqueResult {
  verdict: 'APPROVED' | 'NEEDS_REVISION'
  summary: string
  issues: CritiqueIssue[]
  suggestions: string[]
}

export interface DraftTask {
  id: string
  title: string
  description: string
  acceptance_criteria: string[]
  target_files: string[]
  depends_on: string[]
  task_type: 'backend' | 'frontend'
  warnings?: string[]
  complexity?: 'simple' | 'standard' | 'complex' | null
}

type Phase = 'generating' | 'editing' | 'saving' | 'running' | 'done' | 'error'

interface State {
  phase: Phase
  tasks: DraftTask[]
  errorMsg: string
  jobId: string
  rootDir: string    // 프로젝트 루트 = repo_path; tasks.yaml은 항상 rootDir/agent-data/tasks.yaml
  baseBranch: string // git base branch
  agentCount: number
  noPush: boolean   // true: 로컬 커밋만, push/PR 건너뜀
}

type Action =
  | { type: 'DRAFT_DONE'; tasks: DraftTask[] }
  | { type: 'ERROR'; msg: string }
  | { type: 'UPDATE_TASK'; idx: number; task: DraftTask }
  | { type: 'UPDATE_ALL_TASKS'; tasks: DraftTask[] }
  | { type: 'DELETE_TASK'; idx: number }
  | { type: 'ADD_TASK' }
  | { type: 'MOVE_TASK'; idx: number; dir: -1 | 1 }
  | { type: 'SET_ROOT'; path: string }
  | { type: 'SET_BASE_BRANCH'; branch: string }
  | { type: 'SET_AGENT_COUNT'; count: number }
  | { type: 'SET_NO_PUSH'; value: boolean }
  | { type: 'SAVING' }
  | { type: 'RUNNING'; jobId: string }
  | { type: 'DONE' }
  | { type: 'FIX_DEEP_PATHS' }
  | { type: 'DISMISS_WARNING'; taskIdx: number; warningIdx: number }

// ── 깊은 경로 정규화 헬퍼 ──────────────────────────────────────────────────────

/** target_files 경로 하나를 정규화한다.
 * 1. 슬래시 없음 → 그대로  (user.py → user.py)
 * 2. src/ 접두어 먼저 제거  (src/models/user.py → models/user.py)
 * 3. 슬래시 1개 → 1-level 경로 유지  (models/user.py → models/user.py)
 * 4. 슬래시 2개+ → basename만 추출  (app/src/.../FakeMap.kt → FakeMap.kt)
 */
function normalizeTargetPath(f: string): string {
  if (!f.includes('/')) return f
  let path = f.startsWith('src/') ? f.slice(4) : f
  if (!path.includes('/')) return path
  const slashCount = (path.match(/\//g) ?? []).length
  if (slashCount === 1) return path
  return path.split('/').pop()!
}

function sanitizeFilePaths(files: string[]): { files: string[]; changed: boolean } {
  let changed = false
  const sanitized = files.map(f => {
    const result = normalizeTargetPath(f)
    if (result !== f) changed = true
    return result
  })
  const deduped = [...new Set(sanitized)]
  if (deduped.length !== sanitized.length) changed = true
  return { files: deduped, changed }
}

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
    case 'UPDATE_ALL_TASKS':
      return { ...state, tasks: action.tasks }
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
        task_type: 'backend',
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
    case 'SET_BASE_BRANCH':
      return { ...state, baseBranch: action.branch }
    case 'SET_AGENT_COUNT':
      return { ...state, agentCount: Math.max(1, Math.min(8, action.count)) }
    case 'SET_NO_PUSH':
      return { ...state, noPush: action.value }
    case 'SAVING':
      return { ...state, phase: 'saving' }
    case 'RUNNING':
      return { ...state, phase: 'running', jobId: action.jobId }
    case 'DONE':
      return { ...state, phase: 'done' }
    case 'FIX_DEEP_PATHS': {
      const tasks = state.tasks.map(t => {
        const { files, changed } = sanitizeFilePaths(t.target_files)
        if (!changed) return t
        return {
          ...t,
          target_files: files,
          warnings: (t.warnings ?? []).filter(w => !w.startsWith('target_files 깊은 경로 정리')),
        }
      })
      return { ...state, tasks }
    }
    case 'DISMISS_WARNING': {
      const tasks = [...state.tasks]
      const task = { ...tasks[action.taskIdx] }
      task.warnings = (task.warnings ?? []).filter((_, i) => i !== action.warningIdx)
      tasks[action.taskIdx] = task
      return { ...state, tasks }
    }
    default:
      return state
  }
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  contextDoc: string
  draftKey?: string
  onBack: () => void
  onPipelineStarted?: (jobId: string) => void
}

// ── 컴포넌트 ──────────────────────────────────────────────────────────────────

const DRAFT_STATE_PREFIX = 'draft_state_'

function loadSavedState(draftKey: string): State | null {
  try {
    const raw = localStorage.getItem(DRAFT_STATE_PREFIX + draftKey)
    if (!raw) return null
    return JSON.parse(raw) as State
  } catch {
    return null
  }
}

export function TaskDraftPanel({ contextDoc, draftKey = 'default', onBack, onPipelineStarted }: Props) {
  const [modelName, setModelName] = useState<string>('AI')
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([])
  const [showModelModal, setShowModelModal] = useState(false)
  const [showGraphModal, setShowGraphModal] = useState(false)
  const [critiqueStatus, setCritiqueStatus] = useState<'idle' | 'loading' | 'done'>('idle')
  const [critiqueResult, setCritiqueResult] = useState<CritiqueResult | null>(null)
  const [critiqueResetMsg, setCritiqueResetMsg] = useState(false)
  const [applyStatus, setApplyStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [showRunConfirm, setShowRunConfirm] = useState(false)
  const prevTasksRef = useRef<DraftTask[] | null>(null)
  const critiqueAbortedRef = useRef(false)
  const applyJustFiredRef = useRef(false)

  useEffect(() => {
    fetch(`${API_BASE}/api/config`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.model_capable) setModelName(data.model_capable) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch(`${API_BASE}/api/chat/models`)
      .then(r => r.json())
      .then(data => setAvailableModels(data.models ?? []))
      .catch(() => {})
  }, [])

  const saved = loadSavedState(draftKey)
  const [state, dispatch] = useReducer(reducer, saved ?? {
    phase: 'generating',
    tasks: [],
    errorMsg: '',
    jobId: '',
    rootDir: '.',
    baseBranch: 'main',
    agentCount: 1,
    noPush: false,
  })

  // 클라이언트 사이드 순환 참조 감지 (Kahn's algorithm)
  const hasCycle = useMemo(() => {
    const tasks = state.tasks
    const validIds = new Set(tasks.map(t => t.id))
    const inDegree: Record<string, number> = {}
    const adj: Record<string, string[]> = {}
    for (const t of tasks) { inDegree[t.id] = 0; adj[t.id] = [] }
    for (const t of tasks) {
      for (const dep of t.depends_on) {
        if (!validIds.has(dep)) continue
        adj[dep].push(t.id)
        inDegree[t.id]++
      }
    }
    const queue = Object.keys(inDegree).filter(id => inDegree[id] === 0)
    let processed = 0
    while (queue.length > 0) {
      const node = queue.shift()!
      processed++
      for (const neighbor of adj[node]) {
        inDegree[neighbor]--
        if (inDegree[neighbor] === 0) queue.push(neighbor)
      }
    }
    // tasks.length가 아닌 유니크 ID 수와 비교 (중복 ID 오탐 방지)
    return processed !== validIds.size
  }, [state.tasks])

  // editing 중 상태 변경마다 localStorage에 저장
  useEffect(() => {
    if (state.phase === 'editing' || state.phase === 'saving') {
      localStorage.setItem(DRAFT_STATE_PREFIX + draftKey, JSON.stringify(state))
    }
    if (state.phase === 'running' || state.phase === 'done') {
      localStorage.removeItem(DRAFT_STATE_PREFIX + draftKey)
    }
  }, [state, draftKey])

  // 태스크 목록 변경 시 critique 리셋 (DRAFT_DONE 시에는 idle이라 무시됨)
  useEffect(() => {
    if (prevTasksRef.current !== null && critiqueStatus !== 'idle') {
      if (applyJustFiredRef.current) {
        applyJustFiredRef.current = false
        prevTasksRef.current = state.tasks
        return
      }
      setCritiqueStatus('idle')
      setCritiqueResult(null)
      setCritiqueResetMsg(true)
      setApplyStatus('idle')
    }
    prevTasksRef.current = state.tasks
  }, [state.tasks]) // eslint-disable-line react-hooks/exhaustive-deps

  // tasks.yaml은 항상 rootDir/agent-data/tasks.yaml
  const tasksFilePath = state.rootDir === '.'
    ? 'agent-data/tasks.yaml'
    : state.rootDir.replace(/\/+$/, '') + '/agent-data/tasks.yaml'

  // 마운트 시: 저장된 상태가 있으면 생성 스킵, 없으면 새로 시작
  useEffect(() => {
    if (saved) return   // 복원된 상태가 있으면 폴링 불필요
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    let stopped = false

    async function poll(jobId: string) {
      if (stopped) return
      try {
        const res = await fetch(`${API_BASE}/api/tasks/draft/${jobId}`)
        if (!res.ok) throw new Error('잡 조회 실패')
        const data = await res.json()
        if (stopped) return
        if (data.status === 'done') {
          sessionStorage.removeItem(draftKey)
          dispatch({ type: 'DRAFT_DONE', tasks: data.tasks ?? [] })
        } else if (data.status === 'error') {
          sessionStorage.removeItem(draftKey)
          dispatch({ type: 'ERROR', msg: data.error ?? '초안 생성 실패' })
        } else {
          // 아직 running — 2초 후 재폴링
          pollTimer = setTimeout(() => poll(jobId), 2000)
        }
      } catch (e: unknown) {
        if (!stopped) dispatch({ type: 'ERROR', msg: e instanceof Error ? e.message : String(e) })
      }
    }

    async function startOrResume() {
      // 이미 진행 중인 잡이 있으면 재연결
      const existingJobId = sessionStorage.getItem(draftKey)
      if (existingJobId) {
        poll(existingJobId)
        return
      }
      // 새 잡 시작
      try {
        const res = await fetch(`${API_BASE}/api/tasks/draft`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ context_doc: contextDoc }),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }))
          throw new Error(err.detail ?? '초안 생성 시작 실패')
        }
        const data = await res.json()
        if (stopped) return
        sessionStorage.setItem(draftKey, data.job_id)
        poll(data.job_id)
      } catch (e: unknown) {
        if (!stopped) dispatch({ type: 'ERROR', msg: e instanceof Error ? e.message : String(e) })
      }
    }

    startOrResume()
    return () => {
      stopped = true
      if (pollTimer) clearTimeout(pollTimer)
      // job_id는 sessionStorage에 유지 — 돌아왔을 때 재연결
    }
  }, [contextDoc])


  const [browsing, setBrowsing] = useState(false)
  const browseAbortRef = useRef<AbortController | null>(null)

  async function browseRoot() {
    // 이미 진행 중이면 취소
    if (browsing) {
      browseAbortRef.current?.abort()
      setBrowsing(false)
      return
    }
    const controller = new AbortController()
    browseAbortRef.current = controller
    setBrowsing(true)
    try {
      const initial = state.rootDir && state.rootDir !== '.' ? state.rootDir : '~'
      const res = await fetch(
        `${API_BASE}/api/utils/browse?type=folder&initial=${encodeURIComponent(initial)}`,
        { signal: controller.signal },
      )
      const data = await res.json()
      if (!data.cancelled && data.path) dispatch({ type: 'SET_ROOT', path: data.path })
    } catch (e) {
      // AbortError는 정상 취소 — 무시
      if (e instanceof Error && e.name !== 'AbortError') throw e
    } finally {
      setBrowsing(false)
    }
  }

  async function runCritique() {
    setCritiqueStatus('loading')
    setCritiqueResult(null)
    setCritiqueResetMsg(false)
    critiqueAbortedRef.current = false

    let contextDocValue = contextDoc
    if (!contextDocValue) {
      try {
        const repoPath = state.rootDir === '.' ? '.' : state.rootDir
        const docsRes = await fetch(`${API_BASE}/api/utils/context-docs?repo_path=${encodeURIComponent(repoPath)}`)
        if (docsRes.ok) {
          const docsData = await docsRes.json()
          const docs: Array<{ name: string }> = docsData.docs ?? []
          if (docs.length > 0) {
            const contentRes = await fetch(`${API_BASE}/api/utils/context-docs/${encodeURIComponent(docs[0].name)}?repo_path=${encodeURIComponent(repoPath)}`)
            if (contentRes.ok) {
              const cd = await contentRes.json()
              contextDocValue = cd.content ?? ''
            }
          }
        }
      } catch { /* empty contextDoc으로 진행 */ }
    }

    try {
      const startRes = await fetch(`${API_BASE}/api/tasks/critique`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: state.tasks, context_doc: contextDocValue }),
      })
      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({ detail: startRes.statusText }))
        throw new Error(err.detail ?? 'critique 시작 실패')
      }
      const { job_id } = await startRes.json()

      const poll = async (): Promise<void> => {
        if (critiqueAbortedRef.current) return
        const res = await fetch(`${API_BASE}/api/tasks/critique/${job_id}`)
        if (!res.ok) throw new Error('critique 조회 실패')
        const data = await res.json()
        if (data.status === 'done') {
          setCritiqueResult(data.result)
          setCritiqueStatus('done')
        } else if (data.status === 'failed') {
          throw new Error('critique 실패')
        } else {
          await new Promise(r => setTimeout(r, 2000))
          return poll()
        }
      }
      await poll()
    } catch {
      if (!critiqueAbortedRef.current) setCritiqueStatus('idle')
    }
  }

  async function applyCritique() {
    if (!critiqueResult || applyStatus === 'loading') return
    setApplyStatus('loading')
    try {
      const res = await fetch(`${API_BASE}/api/tasks/critique/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks: state.tasks,
          critique: critiqueResult,
          context_doc: contextDoc,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error((err as { detail?: string }).detail ?? '적용 실패')
      }
      const data = await res.json() as { tasks: DraftTask[]; change_summary: string }
      applyJustFiredRef.current = true
      dispatch({ type: 'UPDATE_ALL_TASKS', tasks: data.tasks })
      setApplyStatus('done')
    } catch {
      setApplyStatus('error')
    }
  }

  async function handleSaveAndRun(
    providerFast: string, modelFast: string,
    providerCapable: string, modelCapable: string,
    agentCount: number,
    roleModels?: Record<string, {provider?: string; model?: string}>,
    noPush?: boolean,
    autoSelectByComplexity?: boolean,
    interventionAutoSplit?: boolean,
  ) {
    setShowModelModal(false)
    dispatch({ type: 'SAVING' })
    try {
      // 1. 컨텍스트 문서를 agent-data/context/spec.md에 저장
      await fetch(`${API_BASE}/api/utils/save-context-doc`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_path: state.rootDir === '.' ? '.' : state.rootDir,
          filename: 'spec.md',
          content: contextDoc,
        }),
      })

      // 2. tasks.yaml 저장 (rootDir/agent-data/tasks.yaml)
      const saveRes = await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: state.tasks, tasks_path: tasksFilePath }),
      })
      if (!saveRes.ok) {
        const err = await saveRes.json().catch(() => ({ detail: saveRes.statusText }))
        throw new Error(err.detail ?? '저장 실패')
      }

      // 3. 파이프라인 실행
      const runRes = await fetch(`${API_BASE}/api/pipeline/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks_path: tasksFilePath,
          repo_path: state.rootDir === '.' ? '.' : state.rootDir,
          base_branch: state.baseBranch || 'main',
          no_pr: false,
          no_push: noPush ?? state.noPush,
          max_workers: agentCount,
          provider_fast: providerFast,
          model_fast: modelFast,
          provider_capable: providerCapable,
          model_capable: modelCapable,
          auto_select_by_complexity: autoSelectByComplexity ?? false,
          intervention_auto_split: interventionAutoSplit ?? false,
          ...(roleModels && Object.keys(roleModels).length > 0 ? { role_models: roleModels } : {}),
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
        <p className="text-sm text-gray-500 dark:text-zinc-400">{modelName}이 태스크를 생성하고 있습니다…</p>
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
    <>
    {showModelModal && (
      <PipelineModelModal
        models={availableModels}
        tasks={state.tasks}
        onConfirm={(pf, mf, pc, mc, ac, rm, np, abc) => handleSaveAndRun(pf, mf, pc, mc, ac, rm, np, abc)}
        onCancel={() => setShowModelModal(false)}
      />
    )}
    {showGraphModal && (
      <DependencyGraphModal
        tasks={state.tasks}
        onClose={() => setShowGraphModal(false)}
        onApply={fixedTasks => {
          dispatch({ type: 'UPDATE_ALL_TASKS', tasks: fixedTasks })
          setShowGraphModal(false)
        }}
      />
    )}
    {showRunConfirm && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
        <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl p-6 max-w-sm w-full mx-4">
          <p className="text-sm font-medium text-gray-800 dark:text-zinc-100 mb-2">
            Momus 검토를 건너뛰시겠어요?
          </p>
          <p className="text-xs text-gray-500 dark:text-zinc-400 mb-5">
            검토 없이 파이프라인을 바로 실행할 수 있습니다.
          </p>
          <div className="flex gap-2 justify-end">
            <button
              className="rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-1.5 text-sm text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800"
              onClick={() => { setShowRunConfirm(false); void runCritique() }}
            >
              🦉 검토 먼저
            </button>
            <button
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
              onClick={() => { setShowRunConfirm(false); setShowModelModal(true) }}
            >
              그냥 실행
            </button>
          </div>
        </div>
      </div>
    )}
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700">
        <div className="flex items-center gap-2">
          <button
            className="text-sm text-gray-500 dark:text-zinc-400 hover:text-gray-700 dark:hover:text-zinc-200"
            onClick={() => {
              localStorage.removeItem(DRAFT_STATE_PREFIX + draftKey)
              onBack()
            }}
          >
            ←
          </button>
          <span className="text-sm font-semibold text-gray-800 dark:text-zinc-100">
            태스크 초안 ({state.tasks.length}개)
          </span>
          {state.tasks.some(t => t.task_type === 'frontend') && (
            <span className="text-xs font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400 px-2 py-0.5 rounded-full">
              🖥 frontend {state.tasks.filter(t => t.task_type === 'frontend').length}개 제외
            </span>
          )}
          {state.tasks.some(t => (t.warnings?.length ?? 0) > 0) && (
            <span className="text-xs font-medium bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400 px-2 py-0.5 rounded-full">
              ⚠ 크기 초과 태스크 있음
            </span>
          )}
          {state.tasks.some(t => t.warnings?.some(w => w.startsWith('target_files 깊은 경로 정리'))) && (
            <button
              className="text-xs font-medium bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400 px-2 py-0.5 rounded-full hover:bg-amber-200 dark:hover:bg-amber-800/60 transition-colors"
              onClick={() => dispatch({ type: 'FIX_DEEP_PATHS' })}
              title="모든 태스크의 target_files 깊은 경로를 파일명만 남기도록 일괄 수정합니다"
            >
              ⚠ 깊은 경로 자동 수정
            </button>
          )}
          {hasCycle && (
            <span className="text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400 px-2 py-0.5 rounded-full animate-pulse">
              ⚠ 순환 참조
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* 의존성 그래프 */}
          <button
            onClick={() => setShowGraphModal(true)}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              hasCycle
                ? 'border-red-400 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 hover:bg-red-100 dark:hover:bg-red-900/40'
                : 'border-gray-300 dark:border-zinc-600 text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800'
            }`}
            title="의존성 DAG 그래프 편집"
          >
            {hasCycle ? '⚠ 의존성 그래프 수정' : '의존성 그래프'}
          </button>
          {/* 프로젝트 루트 (= repo_path, tasks는 rootDir/agent-data/tasks.yaml 고정) */}
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-400 dark:text-zinc-500 shrink-0">프로젝트 루트</span>
            <div className="flex flex-col">
              <input
                className="text-xs rounded border border-gray-300 dark:border-zinc-600 px-2 py-1 bg-white dark:bg-zinc-800 text-gray-600 dark:text-zinc-300 w-52"
                value={state.rootDir === '.' ? '' : state.rootDir}
                onChange={e => dispatch({ type: 'SET_ROOT', path: e.target.value || '.' })}
                placeholder="/path/to/project"
                title="프로젝트 루트 디렉토리 (tasks.yaml은 여기/agent-data/tasks.yaml에 저장됩니다)"
              />
              <span className="text-[10px] text-gray-400 dark:text-zinc-600 px-0.5 mt-0.5 truncate w-52">
                → {tasksFilePath}
              </span>
            </div>
            <button
              onClick={browseRoot}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 px-1 py-1 rounded transition-colors"
              title={browsing ? '취소 (클릭)' : '파인더에서 프로젝트 루트 선택'}
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
          {/* 기본 브랜치 */}
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-400 dark:text-zinc-500 shrink-0">브랜치</span>
            <input
              className="text-xs rounded border border-gray-300 dark:border-zinc-600 px-2 py-1 bg-white dark:bg-zinc-800 text-gray-600 dark:text-zinc-300 w-20"
              value={state.baseBranch}
              onChange={e => dispatch({ type: 'SET_BASE_BRANCH', branch: e.target.value })}
              placeholder="main"
              title="git base branch (main, master, dev 등)"
            />
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
          {/* push 건너뜀 토글 */}
          <button
            className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors ${
              state.noPush
                ? 'border-orange-400 bg-orange-50 text-orange-700 dark:border-orange-600 dark:bg-orange-900/20 dark:text-orange-400'
                : 'border-gray-300 dark:border-zinc-600 text-gray-500 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800'
            }`}
            onClick={() => dispatch({ type: 'SET_NO_PUSH', value: !state.noPush })}
            title={state.noPush
              ? '현재: 로컬 커밋만 (push/PR 건너뜀) — 클릭 시 push + PR 활성화'
              : '현재: push + PR 생성 — 클릭 시 로컬 커밋만'}
          >
            <span>{state.noPush ? '📦 로컬만' : '🚀 push+PR'}</span>
          </button>
          {/* Momus 검토 */}
          <button
            className="rounded-lg border border-indigo-300 dark:border-indigo-600 px-3 py-1.5 text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 disabled:opacity-50 transition-colors"
            onClick={() => void runCritique()}
            disabled={critiqueStatus === 'loading' || state.tasks.length === 0 || state.phase === 'saving'}
          >
            {critiqueStatus === 'loading' ? (
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-3 border border-indigo-400 border-t-transparent rounded-full animate-spin" />
                검토 중…
              </span>
            ) : '🦉 Momus 검토'}
          </button>
          {/* 파이프라인 실행 */}
          <div className="flex flex-col items-end">
            <button
              className="rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
              onClick={() => {
                if (critiqueStatus === 'idle' && applyStatus !== 'done') {
                  setShowRunConfirm(true)
                } else {
                  setShowModelModal(true)
                }
              }}
              disabled={state.phase === 'saving' || state.tasks.length === 0}
            >
              {state.phase === 'saving' ? '저장 중…' : '저장 & 파이프라인 시작 🚀'}
            </button>
            {critiqueStatus === 'done' && critiqueResult?.verdict === 'NEEDS_REVISION' && (
              <span className="text-[10px] text-amber-600 dark:text-amber-400 mt-0.5">
                ⚠️ Momus가 수정을 제안했습니다
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 태스크 목록 */}
      <div className="flex-1 overflow-auto p-4 space-y-3">
        {/* critique 리셋 알림 */}
        {critiqueResetMsg && (
          <p className="text-xs text-gray-400 dark:text-zinc-500 italic -mb-1">
            태스크가 변경되어 이전 검토 결과가 초기화됐습니다.
          </p>
        )}
        {/* critique 결과 배너 */}
        {critiqueStatus === 'done' && critiqueResult && (
          <div className={`rounded-lg border p-3 ${
            critiqueResult.verdict === 'APPROVED'
              ? 'bg-green-50 dark:bg-green-900/20 border-green-300 dark:border-green-700'
              : 'bg-amber-50 dark:bg-amber-900/20 border-amber-300 dark:border-amber-700'
          }`}>
            <div className="flex items-start justify-between gap-2">
              <p className={`text-xs font-medium flex-1 ${
                critiqueResult.verdict === 'APPROVED'
                  ? 'text-green-700 dark:text-green-400'
                  : 'text-amber-700 dark:text-amber-400'
              }`}>
                {critiqueResult.verdict === 'APPROVED'
                  ? `✅ Momus: 태스크 구조 승인 — ${critiqueResult.summary}`
                  : `⚠️ Momus: 수정 필요 — ${critiqueResult.summary}`}
              </p>
              <button
                onClick={() => void applyCritique()}
                disabled={applyStatus === 'loading' || state.tasks.length === 0}
                className="shrink-0 rounded border border-indigo-300 dark:border-indigo-600 px-2.5 py-1 text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 disabled:opacity-50 transition-colors"
              >
                {applyStatus === 'loading' ? (
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-3 h-3 border border-indigo-400 border-t-transparent rounded-full animate-spin" />
                    적용 중…
                  </span>
                ) : applyStatus === 'done' ? '✓ 적용됨' : '제안 적용'}
              </button>
            </div>
            {applyStatus === 'error' && (
              <p className="mt-1 text-xs text-red-500 dark:text-red-400">적용 실패. 직접 수정하세요.</p>
            )}
            {critiqueResult.issues.filter(i => i.task_id === 'GLOBAL').length > 0 && (
              <div className="mt-2 space-y-1" data-testid="global-issues">
                {critiqueResult.issues.filter(i => i.task_id === 'GLOBAL').map((issue, i) => (
                  <div key={i} className={`flex items-start gap-1.5 text-xs ${
                    issue.severity === 'ERROR' ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400'
                  }`}>
                    <span className={`shrink-0 px-1 py-0.5 rounded text-[9px] font-medium border ${
                      issue.severity === 'ERROR'
                        ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20'
                        : 'border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20'
                    }`}>[{issue.category}]</span>
                    <span>{issue.message}</span>
                  </div>
                ))}
              </div>
            )}
            {critiqueResult.suggestions.length > 0 && (
              <details className="mt-2">
                <summary className="text-xs cursor-pointer text-gray-500 dark:text-zinc-400 hover:text-gray-700 dark:hover:text-zinc-200 select-none">
                  제안 사항 ({critiqueResult.suggestions.length}개)
                </summary>
                <ul className="mt-1 space-y-0.5 pl-3">
                  {critiqueResult.suggestions.map((s, i) => (
                    <li key={i} className="text-xs text-gray-600 dark:text-zinc-400">• {s}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        {state.tasks.map((task, idx) => (
          <TaskCard
            key={task.id}
            task={task}
            idx={idx}
            total={state.tasks.length}
            onUpdate={t => dispatch({ type: 'UPDATE_TASK', idx, task: t })}
            onDelete={() => dispatch({ type: 'DELETE_TASK', idx })}
            onMove={dir => dispatch({ type: 'MOVE_TASK', idx, dir })}
            onDismissWarning={wIdx => dispatch({ type: 'DISMISS_WARNING', taskIdx: idx, warningIdx: wIdx })}
            critiqueIssues={critiqueResult?.issues.filter(
              issue => issue.task_id !== 'GLOBAL' && issue.task_id === task.id
            )}
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
    </>
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
  onDismissWarning: (warningIdx: number) => void
  critiqueIssues?: CritiqueIssue[]
}

function TaskCard({ task, idx, total, onUpdate, onDelete, onMove, onDismissWarning, critiqueIssues }: CardProps) {
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
        <button
          className={`shrink-0 text-[10px] font-medium px-2 py-0.5 rounded-full border transition-colors ${
            task.task_type === 'frontend'
              ? 'bg-purple-100 text-purple-700 border-purple-300 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-700'
              : 'bg-gray-100 text-gray-500 border-gray-300 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-600'
          }`}
          onClick={() => updateField('task_type', task.task_type === 'frontend' ? 'backend' : 'frontend')}
          title={task.task_type === 'frontend' ? '프론트엔드 태스크 (파이프라인 제외) — 클릭하여 전환' : '백엔드 태스크 (파이프라인 실행) — 클릭하여 전환'}
        >
          {task.task_type === 'frontend' ? '🖥 frontend' : '⚙ backend'}
        </button>
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

      {/* target_files */}
      <div>
        <p className={`text-xs font-medium mb-1 ${task.target_files.length > 3 ? 'text-amber-600 dark:text-amber-400' : 'text-gray-500 dark:text-zinc-400'}`}>
          대상 파일 ({task.target_files.length}개){task.target_files.length > 3 ? ' ⚠ 3개 초과' : ''}
        </p>
        <div className="flex flex-wrap gap-1">
          {task.target_files.map((f, i) => (
            <span
              key={i}
              className="text-[10px] font-mono bg-gray-100 dark:bg-zinc-800 text-gray-600 dark:text-zinc-400 px-1.5 py-0.5 rounded"
            >
              {f.split('/').pop()}
            </span>
          ))}
        </div>
      </div>

      {/* 경고 */}
      {(task.warnings?.length ?? 0) > 0 && (
        <div className="rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-2 space-y-1">
          {task.warnings!.map((w, i) => (
            <div key={i} className="flex items-start gap-1">
              <p className="flex-1 text-xs text-amber-700 dark:text-amber-400">⚠ {w}</p>
              {w.startsWith('target_files 깊은 경로 정리') && (
                <button
                  className="shrink-0 text-[10px] text-amber-600 dark:text-amber-400 hover:text-amber-800 dark:hover:text-amber-200 underline leading-4"
                  onClick={() => {
                    const { files } = sanitizeFilePaths(task.target_files)
                    onUpdate({
                      ...task,
                      target_files: files,
                      warnings: (task.warnings ?? []).filter((_, j) => j !== i),
                    })
                  }}
                  title="이 태스크의 경로를 정규화합니다 (src/ 제거, 1-level 유지, 깊은 경로는 파일명만 추출)"
                >
                  수정
                </button>
              )}
              <button
                className="shrink-0 text-amber-400 hover:text-amber-700 dark:hover:text-amber-200 leading-4 px-0.5"
                onClick={() => onDismissWarning(i)}
                title="경고 닫기"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* depends_on */}
      {task.depends_on.length > 0 && (
        <p className="text-xs text-gray-400 dark:text-zinc-500">
          선행 태스크: {task.depends_on.join(', ')}
        </p>
      )}

      {/* critique 이슈 (해당 태스크) */}
      {(critiqueIssues?.length ?? 0) > 0 && (
        <div className="space-y-1">
          {critiqueIssues!.map((issue, i) => (
            <div key={i} className={`flex items-start gap-1.5 text-xs rounded border px-2 py-1.5 ${
              issue.severity === 'ERROR'
                ? 'text-red-600 dark:text-red-400 border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20'
                : 'text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10'
            }`}>
              <span className={`shrink-0 px-1 py-0.5 rounded text-[9px] font-medium border ${
                issue.severity === 'ERROR'
                  ? 'border-red-300 dark:border-red-700'
                  : 'border-amber-300 dark:border-amber-700'
              }`}>[{issue.category}]</span>
              <span>{issue.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
