import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { projectTasksPath, projectReportsDir } from '../storage/projectStorage'
import { ACTIVE_JOB_KEY } from './PipelineLogView'
import { AvailableModel, PipelineModelModal } from './PipelineModelModal'
import { DependencyGraphModal } from './DependencyGraphModal'
import type { DraftTask } from './TaskDraftPanel'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string
const RECENT_KEY = 'dashboard_recent_projects'
const MAX_RECENT = 5

// ── 타입 ─────────────────────────────────────────────────────────────────────

interface OutlierTask {
  task_id: string
  reason: 'high_iteration_count' | 'high_single_iteration_tokens' | string
  value: number
  role: string
}

interface Summary {
  task_status: Record<string, number>
  metrics: {
    total_tasks_run: number
    completed: number
    failed: number
    success_rate: number
    approved: number
    total_tests: number
    total_retries: number
    avg_elapsed_seconds: number
    first_try_rate: number
    total_tokens: number
    total_cost_usd: number
  }
  cost_estimation_quality_breakdown?: { exact: number; fallback: number; missing: number }
  models_with_missing_pricing?: string[]
  milestone_count: number
  outlier_tasks?: OutlierTask[]
}

interface DashboardTask {
  id: string
  title: string
  description: string
  acceptance_criteria: string[]
  failure_reason: string
  status: string
  depends_on: string[]
  pr_url: string
  complexity?: 'simple' | 'standard' | 'complex' | null
  report: {
    test_count: number
    retry_count: number
    reviewer_verdict: string
    time_elapsed_seconds: number
    completed_at: string
    total_tokens: number
    cost_usd: number | null
  } | null
}

interface Milestone {
  filename: string
  path: string
  created_at: string
}

interface ContextDoc {
  name: string
  size: number
}

interface WeeklyReportMeta {
  year: number
  week: number
  path: string
}

interface ProjectConfig {
  reportsDir: string
  tasksPath: string
}

// ── 헬퍼 ─────────────────────────────────────────────────────────────────────

function loadRecent(): ProjectConfig[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]')
  } catch {
    return []
  }
}

function saveRecent(cfg: ProjectConfig) {
  const prev = loadRecent().filter(
    r => r.reportsDir !== cfg.reportsDir || r.tasksPath !== cfg.tasksPath
  )
  localStorage.setItem(RECENT_KEY, JSON.stringify([cfg, ...prev].slice(0, MAX_RECENT)))
}

function buildQuery(cfg: ProjectConfig) {
  const p = new URLSearchParams({ reports_dir: cfg.reportsDir, tasks_path: cfg.tasksPath })
  return p.toString()
}

function buildReportsQuery(cfg: ProjectConfig) {
  const p = new URLSearchParams({ reports_dir: cfg.reportsDir })
  return p.toString()
}

// ── 아이콘 ────────────────────────────────────────────────────────────────────

function FolderIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
      <path d="M2 6a2 2 0 012-2h4l2 2h6a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
    </svg>
  )
}

function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
      <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
    </svg>
  )
}

// ── 서브 컴포넌트 ─────────────────────────────────────────────────────────────

function ModelInfoRow({
  lastJob,
  serverConfig,
}: {
  lastJob: any
  serverConfig: { llm_provider: string; model_fast: string; model_capable: string } | null
}) {
  const req = lastJob?.request

  const agentProvider = req?.provider_fast || req?.provider || serverConfig?.llm_provider || ''
  const agentModel    = req?.model_fast    || serverConfig?.model_fast    || ''
  const orchProvider  = req?.provider_capable || req?.provider || serverConfig?.llm_provider || ''
  const orchModel     = req?.model_capable || serverConfig?.model_capable || ''

  if (!agentModel && !orchModel) return null

  const isRunning = lastJob?.status === 'running'
  const isFromJob = !!req

  const modelName = (id: string) => {
    // "claude-sonnet-4-6" → "Sonnet 4.6" 등 짧게 표시
    const map: Record<string, string> = {
      'claude-haiku-4-5-20251001': 'Haiku 4.5',
      'claude-sonnet-4-6': 'Sonnet 4.6',
      'claude-opus-4-6': 'Opus 4.6',
      'gpt-4.1': 'GPT-4.1',
      'gpt-4.1-mini': 'GPT-4.1 Mini',
      'gpt-4o-mini': 'GPT-4o Mini',
      'gpt-4o': 'GPT-4o',
    }
    return map[id] || id
  }

  const roleLabels: Record<string, string> = {
    test_writer: '테스트 작성',
    implementer: '구현',
    reviewer: '리뷰',
  }

  // role_models에서 기본값(agentModel)과 다른 항목만 추출
  const roleOverrides: { label: string; provider: string; model: string }[] = []
  const roleModels: Record<string, { provider?: string; model?: string }> = req?.role_models ?? {}
  for (const [key, val] of Object.entries(roleModels)) {
    const rModel = val?.model || ''
    const rProvider = val?.provider || agentProvider
    if (rModel && (rModel !== agentModel || rProvider !== agentProvider)) {
      roleOverrides.push({ label: roleLabels[key] ?? key, provider: rProvider, model: rModel })
    }
  }

  return (
    <div className="flex items-center gap-2 mt-1.5 flex-wrap">
      {/* 출처 뱃지 */}
      {isRunning ? (
        <span className="flex items-center gap-1 text-[10px] font-medium text-green-600 dark:text-green-400">
          <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse inline-block" />
          실행 중
        </span>
      ) : isFromJob ? (
        <span className="text-[10px] text-gray-400 dark:text-zinc-500">마지막 실행</span>
      ) : (
        <span className="text-[10px] text-gray-400 dark:text-zinc-500">서버 기본값</span>
      )}

      {/* 코딩 에이전트 */}
      <span className="flex items-center gap-1 text-[10px] bg-gray-100 dark:bg-zinc-800 text-gray-600 dark:text-zinc-300 px-2 py-0.5 rounded-full">
        <span className="text-gray-400 dark:text-zinc-500">에이전트</span>
        <span className="font-medium">{agentProvider} / {modelName(agentModel)}</span>
      </span>

      {/* 오케스트레이터 */}
      <span className="flex items-center gap-1 text-[10px] bg-gray-100 dark:bg-zinc-800 text-gray-600 dark:text-zinc-300 px-2 py-0.5 rounded-full">
        <span className="text-gray-400 dark:text-zinc-500">오케스트레이터</span>
        <span className="font-medium">{orchProvider} / {modelName(orchModel)}</span>
      </span>

      {/* 역할별 오버라이드 모델 (기본값과 다를 때만 표시) */}
      {roleOverrides.map(({ label, provider, model }) => (
        <span key={label} className="flex items-center gap-1 text-[10px] bg-blue-50 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300 px-2 py-0.5 rounded-full">
          <span className="text-blue-400 dark:text-blue-500">{label}</span>
          <span className="font-medium">{provider} / {modelName(model)}</span>
        </span>
      ))}
    </div>
  )
}

function MetricCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-700 rounded-xl p-4">
      <p className="text-xs text-gray-500 dark:text-zinc-400 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-900 dark:text-zinc-100">{value}</p>
      {sub && <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">{sub}</p>}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    done: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400',
    failed: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400',
    implementing: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400',
    reviewing: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400',
    pending: 'bg-gray-100 text-gray-600 dark:bg-zinc-800 dark:text-zinc-400',
  }
  const cls = map[status] ?? map.pending
  return (
    <span className={`inline-block text-[11px] font-medium px-2 py-0.5 rounded-full ${cls}`}>
      {status}
    </span>
  )
}

// PR 생성 + COMPLETED 로 이어지는 "승인" verdict 집합. backend 의
// reports.task_report.APPROVED_VERDICTS 와 의미가 일치해야 한다.
const APPROVED_VERDICTS = new Set(['APPROVED', 'APPROVED_WITH_SUGGESTIONS'])

function VerdictBadge({ verdict }: { verdict: string }) {
  if (!verdict) return null
  const approved = APPROVED_VERDICTS.has(verdict)
  return (
    <span className={`inline-block text-[11px] font-medium px-2 py-0.5 rounded-full ${
      approved
        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400'
        : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400'
    }`}>
      {verdict}
    </span>
  )
}

// ── 프로젝트 선택 바 ──────────────────────────────────────────────────────────

interface ProjectBarProps {
  config: ProjectConfig
  onChange: (cfg: ProjectConfig) => void
  onLoad: () => void
  loading: boolean
}

function ProjectBar({ config, onChange, onLoad, loading }: ProjectBarProps) {
  const [showRecent, setShowRecent] = useState(false)
  const [browsing, setBrowsing] = useState<'reports' | 'tasks' | null>(null)
  const recent = loadRecent()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!showRecent) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setShowRecent(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [showRecent])

  async function browseFolder() {
    setBrowsing('reports')
    try {
      const initial = config.reportsDir || '~'
      const res = await fetch(`${API_BASE}/api/utils/browse?type=folder&initial=${encodeURIComponent(initial)}`)
      const data = await res.json()
      if (!data.cancelled && data.path) onChange({ ...config, reportsDir: data.path })
    } finally {
      setBrowsing(null)
    }
  }

  async function browseFile() {
    setBrowsing('tasks')
    try {
      const initial = config.tasksPath || '~'
      const res = await fetch(`${API_BASE}/api/utils/browse?type=file&initial=${encodeURIComponent(initial)}`)
      const data = await res.json()
      if (!data.cancelled && data.path) onChange({ ...config, tasksPath: data.path })
    } finally {
      setBrowsing(null)
    }
  }

  return (
    <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 flex-shrink-0 flex-wrap">
      <span className="text-xs text-gray-500 dark:text-zinc-400 shrink-0">레포</span>
      <div className="flex items-center gap-1">
        <input
          className="text-xs rounded border border-gray-300 dark:border-zinc-600 px-2 py-1 bg-white dark:bg-zinc-800 text-gray-700 dark:text-zinc-300 w-64"
          value={config.reportsDir}
          onChange={e => onChange({ ...config, reportsDir: e.target.value })}
          placeholder="/path/to/project/agent-data/reports"
          title="reports_dir — 프로젝트의 Task Report 디렉토리"
          onKeyDown={e => { if (e.key === 'Enter') onLoad() }}
        />
        <button
          onClick={browseFolder}
          disabled={browsing !== null}
          className="text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 disabled:opacity-40 px-1 py-1 rounded transition-colors"
          title="파인더에서 폴더 선택"
        >
          {browsing === 'reports' ? (
            <span className="inline-block w-4 h-4 border border-gray-400 border-t-transparent rounded-full animate-spin" />
          ) : (
            <FolderIcon />
          )}
        </button>
      </div>
      <span className="text-xs text-gray-500 dark:text-zinc-400 shrink-0">tasks</span>
      <div className="flex items-center gap-1">
        <input
          className="text-xs rounded border border-gray-300 dark:border-zinc-600 px-2 py-1 bg-white dark:bg-zinc-800 text-gray-700 dark:text-zinc-300 w-48"
          value={config.tasksPath}
          onChange={e => onChange({ ...config, tasksPath: e.target.value })}
          placeholder="agent-data/tasks.yaml"
          title="tasks_path — 프로젝트의 tasks.yaml 경로"
          onKeyDown={e => { if (e.key === 'Enter') onLoad() }}
        />
        <button
          onClick={browseFile}
          disabled={browsing !== null}
          className="text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 disabled:opacity-40 px-1 py-1 rounded transition-colors"
          title="파인더에서 파일 선택"
        >
          {browsing === 'tasks' ? (
            <span className="inline-block w-4 h-4 border border-gray-400 border-t-transparent rounded-full animate-spin" />
          ) : (
            <FileIcon />
          )}
        </button>
      </div>
      <button
        onClick={onLoad}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
      >
        {loading ? '로딩 중…' : '불러오기'}
      </button>

      {/* 최근 프로젝트 드롭다운 */}
      {recent.length > 0 && (
        <div className="relative" ref={ref}>
          <button
            onClick={() => setShowRecent(v => !v)}
            className="text-xs text-gray-400 dark:text-zinc-500 hover:text-gray-600 dark:hover:text-zinc-300 px-2 py-1 rounded border border-gray-200 dark:border-zinc-700"
          >
            최근 ▾
          </button>
          {showRecent && (
            <div className="absolute left-0 top-8 z-20 bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 rounded-lg shadow-xl py-1 w-72">
              {recent.map((r, i) => (
                <button
                  key={i}
                  onClick={() => { onChange(r); setShowRecent(false) }}
                  className="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-zinc-700 transition-colors"
                >
                  <p className="text-xs text-gray-700 dark:text-zinc-200 truncate">{r.reportsDir}</p>
                  <p className="text-[10px] text-gray-400 dark:text-zinc-500 truncate">{r.tasksPath}</p>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────

interface ActiveJob {
  job_id: string
  status: string
  paused?: boolean
  stopping?: boolean
}

interface DashboardPageProps {
  project?: { name: string; rootDir: string; baseBranch?: string; discordChannelId?: string }
  onBack?: () => void
  onPipelineStarted?: (jobId: string) => void
  onDiscordChannelCreated?: (channelId: string) => void
}

function pathsOverlap(a: string, b: string): boolean {
  if (!a || !b) return false
  return a === b || a.endsWith('/' + b) || b.endsWith('/' + a)
}

/** 채널 ID가 워커에서 생성될 때까지 status 폴링 후 콜백 호출 (최대 15초) */
async function pollDiscordChannelId(
  jobId: string,
  currentChannelId: string | undefined,
  onCreated: (id: string) => void,
) {
  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000))
    try {
      const data = await fetch(`${API_BASE}/api/pipeline/status/${jobId}`).then(r => r.json())
      if (data.discord_channel_id && data.discord_channel_id !== currentChannelId) {
        onCreated(data.discord_channel_id)
        return
      }
    } catch { return }
  }
}

export function DashboardPage({ project, onBack, onPipelineStarted, onDiscordChannelCreated }: DashboardPageProps = {}) {
  const [config, setConfig] = useState<ProjectConfig>(() => {
    if (project) {
      return {
        reportsDir: projectReportsDir(project),
        tasksPath: projectTasksPath(project),
      }
    }
    const recent = loadRecent()
    return recent[0] ?? { reportsDir: 'agent-data/reports', tasksPath: 'agent-data/tasks.yaml' }
  })
  const [summary, setSummary] = useState<Summary | null>(null)
  const [tasks, setTasks] = useState<DashboardTask[]>([])
  const [milestones, setMilestones] = useState<Milestone[]>([])
  const [selectedMilestone, setSelectedMilestone] = useState<{ filename: string; content: string } | null>(null)
  const [contextDocs, setContextDocs] = useState<ContextDoc[]>([])
  const [selectedContextDoc, setSelectedContextDoc] = useState<{ name: string; content: string } | null>(null)
  const [weeklyReports, setWeeklyReports] = useState<WeeklyReportMeta[]>([])
  const [selectedWeekly, setSelectedWeekly] = useState<{ year: number; week: number; content: string } | null>(null)
  const [generatingWeekly, setGeneratingWeekly] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeJob, setActiveJob] = useState<ActiveJob | null>(null)
  const [lastMatchedJob, setLastMatchedJob] = useState<any>(null)
  const [serverConfig, setServerConfig] = useState<{ llm_provider: string; model_fast: string; model_capable: string } | null>(null)
  const [showResumeModal, setShowResumeModal] = useState(false)
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([])
  const [controlling, setControlling] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<string>>(new Set())
  const [autoMerge, setAutoMerge] = useState<boolean>(() => {
    return localStorage.getItem('pipeline_auto_merge') === 'true'
  })
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState<{ description: string; criteria: string[] }>({ description: '', criteria: [] })
  const [savingTask, setSavingTask] = useState(false)
  const [rerunningTaskId, setRerunningTaskId] = useState<string | null>(null)
  const [redesigningTaskIds, setRedesigningTaskIds] = useState<Set<string>>(new Set())
  const [redesignResults, setRedesignResults] = useState<{ taskId: string; action: string; explanation: string; tasks: Record<string, unknown>[] }[]>([])
  const [applyingRedesign, setApplyingRedesign] = useState(false)
  const [showGraphModal, setShowGraphModal] = useState(false)
  const [fullTasksForGraph, setFullTasksForGraph] = useState<Record<string, unknown>[]>([])
  const [graphLoading, setGraphLoading] = useState(false)

  // 클라이언트 사이드 순환 참조 감지
  const hasCycle = useMemo(() => {
    const validIds = new Set(tasks.map(t => t.id))
    const inDegree: Record<string, number> = {}
    const adj: Record<string, string[]> = {}
    for (const t of tasks) { inDegree[t.id] = 0; adj[t.id] = [] }
    for (const t of tasks) {
      for (const dep of (t.depends_on ?? [])) {
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
  }, [tasks])

  const loadData = async (cfg: ProjectConfig) => {
    setLoading(true)
    setError('')
    setSelectedMilestone(null)
    setSelectedWeekly(null)
    setSelectedContextDoc(null)
    const q = buildQuery(cfg)
    const reportsQ = buildReportsQuery(cfg)
    try {
      const [summaryRes, tasksRes, milestonesRes, weeklyRes] = await Promise.all([
        fetch(`${API_BASE}/api/dashboard/summary?${q}`),
        fetch(`${API_BASE}/api/dashboard/tasks?${q}`),
        fetch(`${API_BASE}/api/dashboard/milestones?${reportsQ}`),
        fetch(`${API_BASE}/api/reports/weekly?${reportsQ}`),
      ])
      if (!summaryRes.ok || !tasksRes.ok || !milestonesRes.ok) {
        throw new Error('API 응답 오류')
      }
      const [s, t, m] = await Promise.all([summaryRes.json(), tasksRes.json(), milestonesRes.json()])
      setSummary(s)
      setTasks(t.tasks ?? [])
      setMilestones(m.milestones ?? [])
      if (weeklyRes.ok) {
        const w = await weeklyRes.json()
        setWeeklyReports(w.reports ?? [])
      }
      saveRecent(cfg)
    } catch {
      setError('대시보드 데이터를 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData(config) }, [])

  useEffect(() => {
    fetch(`${API_BASE}/api/config`)
      .then(r => r.json())
      .then(setServerConfig)
      .catch(() => {})
    fetch(`${API_BASE}/api/chat/models`)
      .then(r => r.json())
      .then(data => setAvailableModels(data.models ?? []))
      .catch(() => {})
  }, [])

  // 컨텍스트 문서 목록 로드 (project.rootDir 기준, 변경 시 재로드)
  const repoPathForContext = project?.rootDir ?? '.'

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/utils/context-docs?repo_path=${encodeURIComponent(repoPathForContext)}`)
      .then(r => r.ok ? r.json() : { docs: [] })
      .then(data => { if (!cancelled) setContextDocs(data.docs ?? []) })
      .catch(() => { if (!cancelled) setContextDocs([]) })
    return () => { cancelled = true }
  }, [repoPathForContext])

  // 프로젝트 매칭 잡 조회 (파이프라인 제어용)
  const fetchActiveJob = useCallback(async () => {
    if (!project) return
    try {
      const data = await fetch(`${API_BASE}/api/pipeline/jobs`).then(r => r.json())
      const jobs = data.jobs ?? []
      const matched = jobs.find((j: any) =>
        pathsOverlap(project.rootDir, j.request?.repo_path ?? '')
      )
      // running 상태인 잡만 제어 대상, 최근 잡은 상태 무관하게 추적
      setActiveJob(matched && matched.status === 'running' ? matched : null)
      setLastMatchedJob(matched ?? null)
    } catch { /* 무시 */ }
  }, [project])

  useEffect(() => {
    fetchActiveJob()
    // 5초마다 갱신 (너무 잦은 폴링 방지)
    const id = setInterval(fetchActiveJob, 5000)
    return () => clearInterval(id)
  }, [fetchActiveJob])

  async function sendControl(action: 'pause' | 'resume' | 'stop') {
    if (!activeJob) return
    setControlling(true)
    try {
      await fetch(`${API_BASE}/api/pipeline/control/${activeJob.job_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
      if (action === 'stop') {
        // 신호는 전달됐지만 파이프라인은 현재 태스크가 끝날 때까지 계속 실행됨.
        // UI에 "중단 요청됨" 상태를 표시하고, 폴링이 done을 감지하면 자연히 사라짐.
        setActiveJob(prev => prev ? { ...prev, stopping: true } : prev)
      } else {
        await fetchActiveJob()
      }
    } finally {
      setControlling(false)
    }
  }

  async function resumePipeline(providerFast: string, modelFast: string, providerCapable: string, modelCapable: string, agentCount: number, roleModels?: Record<string, {provider?: string; model?: string}>, noPush?: boolean, autoSelectByComplexity?: boolean) {
    if (!project) return
    setShowResumeModal(false)
    setResuming(true)
    try {
      const res = await fetch(`${API_BASE}/api/pipeline/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks_path: projectTasksPath(project),
          repo_path: project.rootDir,
          base_branch: project.baseBranch ?? 'main',
          no_pr: false,
          no_push: noPush ?? false,
          max_workers: agentCount,
          discord_channel_id: project.discordChannelId ?? null,
          auto_merge: autoMerge,
          provider_fast: providerFast,
          model_fast: modelFast,
          provider_capable: providerCapable,
          model_capable: modelCapable,
          auto_select_by_complexity: autoSelectByComplexity ?? false,
          ...(roleModels && Object.keys(roleModels).length > 0 ? { role_models: roleModels } : {}),
        }),
      })
      if (!res.ok) throw new Error('파이프라인 시작 실패')
      const data = await res.json()
      // Discord 채널 ID — 즉시 반환되면 저장, 아직 없으면 워커 완료 후 폴링으로 획득
      if (data.discord_channel_id && data.discord_channel_id !== project.discordChannelId) {
        onDiscordChannelCreated?.(data.discord_channel_id)
      } else if (!project.discordChannelId && onDiscordChannelCreated) {
        pollDiscordChannelId(data.job_id, project.discordChannelId, onDiscordChannelCreated)
      }
      // 기존 목록에 추가 (복수 파이프라인 지원)
      const _raw = localStorage.getItem(ACTIVE_JOB_KEY)
      let _ids: string[] = []
      try { const v = JSON.parse(_raw ?? '[]'); _ids = Array.isArray(v) ? v : [String(v)] } catch { _ids = _raw ? [_raw] : [] }
      if (!_ids.includes(data.job_id)) _ids.push(data.job_id)
      localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(_ids))
      onPipelineStarted?.(data.job_id)
    } catch (e) {
      alert(e instanceof Error ? e.message : '오류 발생')
    } finally {
      setResuming(false)
    }
  }

  async function updateTask(taskId: string) {
    setSavingTask(true)
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description: editDraft.description,
          acceptance_criteria: editDraft.criteria,
          tasks_path: config.tasksPath,
        }),
      })
      if (!res.ok) throw new Error('저장 실패')
      const updated = await res.json()
      setTasks(prev => prev.map(t => t.id === taskId ? { ...t, description: updated.description, acceptance_criteria: updated.acceptance_criteria } : t))
      setEditingTaskId(null)
    } catch (e) {
      alert(e instanceof Error ? e.message : '저장 실패')
    } finally {
      setSavingTask(false)
    }
  }

  async function rerunTask(taskId: string) {
    if (!project) return
    setRerunningTaskId(taskId)
    try {
      const res = await fetch(`${API_BASE}/api/pipeline/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks_path: projectTasksPath(project),
          repo_path: project.rootDir,
          base_branch: project.baseBranch ?? 'main',
          task_id: taskId,
          no_pr: false,
          discord_channel_id: project.discordChannelId ?? null,
          auto_merge: autoMerge,
        }),
      })
      if (!res.ok) throw new Error('재실행 시작 실패')
      const data = await res.json()
      if (data.discord_channel_id && data.discord_channel_id !== project.discordChannelId) {
        onDiscordChannelCreated?.(data.discord_channel_id)
      } else if (!project.discordChannelId && onDiscordChannelCreated) {
        pollDiscordChannelId(data.job_id, project.discordChannelId, onDiscordChannelCreated)
      }
      onPipelineStarted?.(data.job_id)
    } catch (e) {
      alert(e instanceof Error ? e.message : '재실행 실패')
    } finally {
      setRerunningTaskId(null)
    }
  }

  async function redesignTask(taskId: string) {
    if (!project) return
    setRedesigningTaskIds(prev => new Set([...prev, taskId]))
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/redesign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks_path: projectTasksPath(project),
          repo_path: project.rootDir,
        }),
      })
      if (!res.ok) throw new Error('재설계 시작 실패')
      const { job_id } = await res.json()

      // 폴링
      for (let i = 0; i < 120; i++) {
        await new Promise(r => setTimeout(r, 2000))
        const poll = await fetch(`${API_BASE}/api/tasks/redesign/${job_id}`)
        const data = await poll.json()
        if (data.status === 'done') {
          setRedesignResults(prev => [...prev, { taskId, action: data.action, explanation: data.explanation, tasks: data.tasks }])
          return
        }
        if (data.status === 'error') {
          alert(`재설계 실패: ${data.error}`)
          return
        }
      }
      alert('재설계 시간 초과')
    } catch (e) {
      alert(e instanceof Error ? e.message : '재설계 실패')
    } finally {
      setRedesigningTaskIds(prev => { const next = new Set(prev); next.delete(taskId); return next })
    }
  }

  async function applyRedesign() {
    const currentResult = redesignResults[0]
    if (!currentResult || !project) return
    setApplyingRedesign(true)
    try {
      const tasksPath = projectTasksPath(project)
      const res = await fetch(`${API_BASE}/api/tasks?tasks_path=${encodeURIComponent(tasksPath)}`)
      if (!res.ok) throw new Error('태스크 목록 로드 실패')
      const { tasks: currentTasks } = await res.json()

      // 원래 태스크를 재설계된 태스크들로 교체
      const newTasks = currentTasks.flatMap((t: Record<string, unknown>) =>
        t.id === currentResult.taskId
          ? currentResult.tasks.map(rt => ({ ...rt, status: 'pending', retry_count: 0, last_error: '', failure_reason: '', pr_url: '' }))
          : [t]
      )

      const saveRes = await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: newTasks, tasks_path: tasksPath }),
      })
      if (!saveRes.ok) throw new Error('태스크 저장 실패')

      setRedesignResults(prev => prev.slice(1))
      await loadData(config)
    } catch (e) {
      alert(e instanceof Error ? e.message : '적용 실패')
    } finally {
      setApplyingRedesign(false)
    }
  }

  async function openDependencyGraph() {
    if (!project) return
    setGraphLoading(true)
    try {
      const tasksPath = projectTasksPath(project)
      const res = await fetch(`${API_BASE}/api/tasks?tasks_path=${encodeURIComponent(tasksPath)}`)
      if (!res.ok) throw new Error('태스크 목록 로드 실패')
      const { tasks: fullTasks } = await res.json()
      setFullTasksForGraph(fullTasks)
      setShowGraphModal(true)
    } catch (e) {
      alert(e instanceof Error ? e.message : '로드 실패')
    } finally {
      setGraphLoading(false)
    }
  }

  async function applyDependencyFix(fixedDraftTasks: DraftTask[]) {
    if (!project) return
    const fixedMap = new Map(fixedDraftTasks.map(t => [t.id, t.depends_on]))
    const updatedTasks = fullTasksForGraph.map(t => ({
      ...t,
      depends_on: fixedMap.get(t.id as string) ?? (t.depends_on as string[]),
    }))
    try {
      const res = await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: updatedTasks, tasks_path: projectTasksPath(project) }),
      })
      if (!res.ok) throw new Error('저장 실패')
      setShowGraphModal(false)
      await loadData(config)
    } catch (e) {
      alert(e instanceof Error ? e.message : '저장 실패')
    }
  }

  const openContextDoc = async (name: string) => {
    try {
      const repoPath = project?.rootDir ?? '.'
      const res = await fetch(`${API_BASE}/api/utils/context-docs/${encodeURIComponent(name)}?repo_path=${encodeURIComponent(repoPath)}`)
      const data = await res.json()
      setSelectedMilestone(null)
      setSelectedWeekly(null)
      setSelectedContextDoc({ name, content: data.content })
    } catch { /* ignore */ }
  }

  const openMilestone = async (filename: string) => {
    try {
      const q = buildReportsQuery(config)
      const res = await fetch(`${API_BASE}/api/dashboard/milestones/${filename}?${q}`)
      const data = await res.json()
      setSelectedWeekly(null)
      setSelectedMilestone({ filename, content: data.content })
    } catch { /* ignore */ }
  }

  const openWeeklyReport = async (year: number, week: number) => {
    try {
      const q = buildReportsQuery(config)
      const res = await fetch(`${API_BASE}/api/reports/weekly/${year}/${week}?${q}`)
      const data = await res.json()
      setSelectedMilestone(null)
      setSelectedWeekly({ year, week, content: data.content })
    } catch { /* ignore */ }
  }

  const generateWeeklyReport = async () => {
    setGeneratingWeekly(true)
    try {
      const res = await fetch(`${API_BASE}/api/reports/weekly`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reports_dir: config.reportsDir }),
      })
      if (!res.ok) throw new Error('생성 실패')
      const data = await res.json()
      setWeeklyReports(prev => {
        const filtered = prev.filter(r => !(r.year === data.year && r.week === data.week))
        return [{ year: data.year, week: data.week, path: data.path }, ...filtered]
      })
      setSelectedMilestone(null)
      setSelectedWeekly({ year: data.year, week: data.week, content: data.content })
    } catch (e) {
      alert(e instanceof Error ? e.message : '주간 보고서 생성 실패')
    } finally {
      setGeneratingWeekly(false)
    }
  }

  const m = summary?.metrics

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 프로젝트 선택 바 (프로젝트 prop 없을 때만) */}
      {!project && (
        <ProjectBar
          config={config}
          onChange={setConfig}
          onLoad={() => loadData(config)}
          loading={loading}
        />
      )}

      {/* 본문 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 메인 대시보드 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* 헤더 */}
          <div>
            {onBack && (
              <button
                onClick={onBack}
                className="flex items-center gap-1 text-xs text-gray-400 dark:text-zinc-500 hover:text-blue-500 dark:hover:text-blue-400 mb-2 transition-colors"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M9.707 16.707a1 1 0 01-1.414 0l-6-6a1 1 0 010-1.414l6-6a1 1 0 011.414 1.414L5.414 9H17a1 1 0 110 2H5.414l4.293 4.293a1 1 0 010 1.414z" clipRule="evenodd" />
                </svg>
                프로젝트 목록
              </button>
            )}
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <h1 className="text-xl font-bold text-gray-900 dark:text-zinc-100">
                  {project ? project.name : '대시보드'}
                </h1>
                <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5 font-mono truncate">
                  {config.reportsDir}
                </p>
                <ModelInfoRow lastJob={lastMatchedJob} serverConfig={serverConfig} />
              </div>

              {/* 파이프라인 재개 버튼 + auto_merge 토글 (실행 중 잡 없을 때) */}
              {project && !activeJob && (
                <div className="flex items-center gap-2 flex-shrink-0">
                  {/* auto_merge 토글 */}
                  <button
                    onClick={() => {
                      const next = !autoMerge
                      setAutoMerge(next)
                      localStorage.setItem('pipeline_auto_merge', String(next))
                    }}
                    className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium border transition-colors ${
                      autoMerge
                        ? 'bg-emerald-50 dark:bg-emerald-900/30 border-emerald-300 dark:border-emerald-700 text-emerald-700 dark:text-emerald-400'
                        : 'bg-gray-50 dark:bg-zinc-800 border-gray-300 dark:border-zinc-600 text-gray-500 dark:text-zinc-400'
                    }`}
                    title={autoMerge ? '그룹 완료 후 base_branch에 자동 머지 ON' : '자동 머지 OFF — PR만 생성'}
                  >
                    <span className={`w-3 h-3 rounded-full border-2 transition-colors ${
                      autoMerge ? 'bg-emerald-500 border-emerald-500' : 'bg-transparent border-gray-400 dark:border-zinc-500'
                    }`} />
                    자동 머지
                  </button>
                  <button
                    onClick={() => setShowResumeModal(true)}
                    disabled={resuming}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                    title="pending/failed 태스크만 이어서 실행"
                  >
                    {resuming ? '시작 중…' : '▶ 파이프라인 재개'}
                  </button>
                </div>
              )}
              {/* 파이프라인 제어 버튼 (실행 중일 때) */}
              {activeJob && (
                <div className="flex items-center gap-2 flex-shrink-0">
                  <div className="flex items-center gap-1.5">
                    {activeJob.stopping ? (
                      <span className="text-xs text-red-400 font-medium">🛑 중단 요청됨…</span>
                    ) : activeJob.paused ? (
                      <span className="text-xs text-amber-400 font-medium">⏸ 일시정지됨</span>
                    ) : (
                      <span className="flex items-center gap-1 text-xs text-green-500 font-medium">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse inline-block" />
                        실행 중
                      </span>
                    )}
                  </div>
                  {/* stopping 중엔 버튼 숨김 */}
                  {!activeJob.stopping && (
                    <>
                      {activeJob.paused ? (
                        <button
                          onClick={() => sendControl('resume')}
                          disabled={controlling}
                          className="rounded-lg px-3 py-1.5 text-xs font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 transition-colors"
                        >
                          ▶ 계속
                        </button>
                      ) : (
                        <button
                          onClick={() => sendControl('pause')}
                          disabled={controlling}
                          className="rounded-lg px-3 py-1.5 text-xs font-medium bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
                        >
                          ⏸ 멈춤
                        </button>
                      )}
                      <button
                        onClick={() => { if (confirm('파이프라인을 중단하시겠습니까?')) sendControl('stop') }}
                        disabled={controlling}
                        className="rounded-lg px-3 py-1.5 text-xs font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                        title="현재 태스크 완료 후 종료"
                      >
                        ■ 중단
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {loading ? (
            <div className="flex items-center gap-2 text-gray-400 dark:text-zinc-500 text-sm">
              <div className="w-4 h-4 border-2 border-gray-300 dark:border-zinc-600 border-t-blue-500 rounded-full animate-spin" />
              불러오는 중…
            </div>
          ) : error ? (
            <div className="text-red-500 text-sm">{error}</div>
          ) : (
            <>
              {/* 단가 미등록 배너 */}
              {summary?.models_with_missing_pricing && summary.models_with_missing_pricing.length > 0 && (
                <div className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/30 px-4 py-3 text-xs text-amber-800 dark:text-amber-200 flex flex-wrap items-start gap-x-2 gap-y-1 min-w-0">
                  <span className="font-semibold shrink-0">일부 모델 단가 미등록</span>
                  <span className="opacity-80 min-w-0 break-words">
                    다음 모델의 단가가 등록되어 있지 않아 비용 집계에서 제외되었습니다:{' '}
                    <code className="font-mono break-all">{summary.models_with_missing_pricing.join(', ')}</code>
                  </span>
                </div>
              )}

              {/* 메트릭 카드 */}
              {m && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <MetricCard label="총 실행 태스크" value={m.total_tasks_run} />
                  <MetricCard
                    label="성공률"
                    value={`${m.success_rate}%`}
                    sub={`${m.completed} 완료 / ${m.failed} 실패`}
                  />
                  <MetricCard
                    label="첫 시도 성공률"
                    value={`${m.first_try_rate}%`}
                    sub="재시도 없이 통과"
                  />
                  <MetricCard
                    label="APPROVED"
                    value={m.approved}
                    sub={`총 ${m.total_tests}개 테스트`}
                  />
                  <MetricCard
                    label="평균 소요 시간"
                    value={`${m.avg_elapsed_seconds}s`}
                    sub="태스크 평균"
                  />
                  <MetricCard label="총 재시도 횟수" value={m.total_retries} />
                  <MetricCard label="마일스톤 보고서" value={summary?.milestone_count ?? 0} />
                  <MetricCard
                    label="총 LLM 비용"
                    value={m.total_cost_usd > 0 ? `$${m.total_cost_usd.toFixed(4)}` : '—'}
                    sub={m.total_tokens > 0 ? `${(m.total_tokens / 1000).toFixed(1)}K 토큰` : undefined}
                  />
                  <MetricCard
                    label="태스크 상태"
                    value={
                      Object.entries(summary?.task_status ?? {})
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(' / ') || '—'
                    }
                  />
                </div>
              )}

              {/* ⚠️ 주의 필요 태스크 — outlier 가 있을 때만 표시 */}
              {summary?.outlier_tasks && summary.outlier_tasks.length > 0 && (
                <div>
                  <h2 className="text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-3">
                    ⚠️ 주의 필요 태스크
                  </h2>
                  <div className="rounded-xl border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 divide-y divide-amber-200 dark:divide-amber-800/60 overflow-hidden">
                    {summary.outlier_tasks.map(o => {
                      const reasonLabel =
                        o.reason === 'high_iteration_count'
                          ? `iteration 과다 (${o.role || '역할 미상'})`
                          : o.reason === 'high_single_iteration_tokens'
                            ? '단일 iteration 토큰 초과'
                            : o.reason
                      return (
                        <button
                          key={`${o.task_id}-${o.reason}`}
                          type="button"
                          onClick={() => {
                            setExpandedTaskIds(prev => {
                              const next = new Set(prev)
                              next.add(o.task_id)
                              return next
                            })
                            // 태스크 목록으로 스크롤
                            const el = document.getElementById(`task-row-${o.task_id}`)
                            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
                          }}
                          className="w-full text-left flex items-center gap-3 px-4 py-2.5 hover:bg-amber-100/70 dark:hover:bg-amber-900/40 transition-colors"
                          title="클릭하면 태스크 상세를 열어줍니다"
                        >
                          <span className="text-xs font-mono text-amber-700 dark:text-amber-300 flex-shrink-0">
                            {o.task_id}
                          </span>
                          <span className="text-xs text-amber-800 dark:text-amber-200 flex-1 min-w-0 truncate">
                            {reasonLabel}
                          </span>
                          <span className="text-xs font-semibold text-amber-900 dark:text-amber-100 flex-shrink-0">
                            {o.value.toLocaleString()}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* 태스크 목록 */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-gray-700 dark:text-zinc-300">태스크 목록</h2>
                  <div className="flex items-center gap-2">
                    {tasks.some(t => t.status === 'failed') && project && (
                      <button
                        onClick={() => tasks.filter(t => t.status === 'failed').forEach(t => redesignTask(t.id))}
                        disabled={tasks.filter(t => t.status === 'failed').every(t => redesigningTaskIds.has(t.id))}
                        className="rounded-lg border border-purple-400 dark:border-purple-600 px-3 py-1 text-xs font-medium text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20 hover:bg-purple-100 dark:hover:bg-purple-900/40 disabled:opacity-50 transition-colors"
                        title="실패한 태스크 전체 AI 재설계"
                      >
                        🤖 전체 AI 재설계
                      </button>
                    )}
                    {tasks.length > 0 && (
                      <button
                        onClick={openDependencyGraph}
                        disabled={graphLoading}
                        className={`rounded-lg border px-3 py-1 text-xs font-medium transition-colors ${
                          hasCycle
                            ? 'border-red-400 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 hover:bg-red-100 dark:hover:bg-red-900/40'
                            : 'border-gray-300 dark:border-zinc-600 text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800'
                        }`}
                        title="의존성 DAG 그래프 편집"
                      >
                        {graphLoading ? '로딩 중…' : hasCycle ? '⚠ 의존성 그래프 수정' : '의존성 그래프'}
                      </button>
                    )}
                  </div>
                </div>
                <div className="border border-gray-200 dark:border-zinc-700 rounded-xl overflow-hidden divide-y divide-gray-100 dark:divide-zinc-800">
                  {tasks.length === 0 ? (
                    <div className="px-4 py-8 text-center text-gray-400 dark:text-zinc-500 text-sm">
                      태스크가 없습니다
                    </div>
                  ) : tasks.map(task => {
                    const expanded = expandedTaskIds.has(task.id)
                    return (
                      <div key={task.id} id={`task-row-${task.id}`}>
                        {/* 요약 행 */}
                        <button
                          onClick={() => setExpandedTaskIds(prev => {
                            const next = new Set(prev)
                            expanded ? next.delete(task.id) : next.add(task.id)
                            return next
                          })}
                          className="w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-zinc-800/50 transition-colors"
                        >
                          {/* 펼침 화살표 */}
                          <svg
                            className={`w-3.5 h-3.5 text-gray-400 dark:text-zinc-500 flex-shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
                            viewBox="0 0 20 20" fill="currentColor"
                          >
                            <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
                          </svg>
                          {/* ID + 타이틀 */}
                          <div className="flex-1 min-w-0 flex items-center gap-2">
                            <span className="text-xs text-gray-400 dark:text-zinc-500 font-mono flex-shrink-0">{task.id}</span>
                            <span className="text-sm text-gray-800 dark:text-zinc-200 font-medium truncate">{task.title}</span>
                          </div>
                          {/* 오른쪽 메타 */}
                          <div className="flex items-center gap-3 flex-shrink-0">
                            <StatusBadge status={task.status} />
                            {task.failure_reason?.startsWith('[MAX_ITER]') && (
                              <span className="text-xs font-semibold px-1.5 py-0.5 rounded bg-orange-100 dark:bg-orange-900/40 text-orange-600 dark:text-orange-400">
                                반복 초과
                              </span>
                            )}
                            <VerdictBadge verdict={task.report?.reviewer_verdict ?? ''} />
                            {task.report && (
                              <span className="text-xs text-gray-400 dark:text-zinc-500">
                                {task.report.time_elapsed_seconds}s
                              </span>
                            )}
                            {task.report && task.report.cost_usd != null && task.report.cost_usd > 0 && (
                              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-mono">
                                ${task.report.cost_usd.toFixed(4)}
                              </span>
                            )}
                            {task.pr_url && (
                              <a
                                href={task.pr_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={e => e.stopPropagation()}
                                className="text-xs text-blue-500 hover:underline"
                              >
                                PR →
                              </a>
                            )}
                          </div>
                        </button>

                        {/* 펼쳐진 세부 내용 */}
                        {expanded && (
                          <div className="px-10 pb-4 pt-1 bg-gray-50 dark:bg-zinc-900/60 space-y-3 text-xs">
                            {editingTaskId === task.id ? (
                              /* 편집 모드 */
                              <>
                                <div>
                                  <p className="text-gray-500 dark:text-zinc-400 font-semibold mb-1">설명</p>
                                  <textarea
                                    value={editDraft.description}
                                    onChange={e => setEditDraft(prev => ({ ...prev, description: e.target.value }))}
                                    rows={4}
                                    className="w-full rounded border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-gray-800 dark:text-zinc-200 px-2 py-1.5 text-xs leading-relaxed resize-y focus:outline-none focus:ring-1 focus:ring-blue-500"
                                  />
                                </div>
                                <div>
                                  <p className="text-gray-500 dark:text-zinc-400 font-semibold mb-1">수락 기준</p>
                                  <div className="space-y-1">
                                    {editDraft.criteria.map((c, i) => (
                                      <div key={i} className="flex gap-1.5">
                                        <input
                                          value={c}
                                          onChange={e => setEditDraft(prev => ({ ...prev, criteria: prev.criteria.map((x, j) => j === i ? e.target.value : x) }))}
                                          className="flex-1 rounded border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-gray-800 dark:text-zinc-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                                        />
                                        <button
                                          onClick={() => setEditDraft(prev => ({ ...prev, criteria: prev.criteria.filter((_, j) => j !== i) }))}
                                          className="text-gray-400 hover:text-red-500 dark:hover:text-red-400 px-1"
                                        >✕</button>
                                      </div>
                                    ))}
                                    <button
                                      onClick={() => setEditDraft(prev => ({ ...prev, criteria: [...prev.criteria, ''] }))}
                                      className="text-blue-500 hover:text-blue-700 dark:hover:text-blue-300"
                                    >+ 항목 추가</button>
                                  </div>
                                </div>
                                <div className="flex gap-2 pt-1">
                                  <button
                                    onClick={() => updateTask(task.id)}
                                    disabled={savingTask}
                                    className="rounded px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                                  >{savingTask ? '저장 중…' : '저장'}</button>
                                  <button
                                    onClick={() => setEditingTaskId(null)}
                                    className="rounded px-3 py-1 bg-gray-200 dark:bg-zinc-700 text-gray-700 dark:text-zinc-300 hover:bg-gray-300 dark:hover:bg-zinc-600 transition-colors"
                                  >취소</button>
                                </div>
                              </>
                            ) : (
                              /* 읽기 모드 */
                              <>
                                {/* description */}
                                {task.description && (
                                  <div>
                                    <p className="text-gray-500 dark:text-zinc-400 font-semibold mb-0.5">설명</p>
                                    <p className="text-gray-700 dark:text-zinc-300 leading-relaxed whitespace-pre-wrap">{task.description}</p>
                                  </div>
                                )}
                                {/* acceptance_criteria */}
                                {task.acceptance_criteria?.length > 0 && (
                                  <div>
                                    <p className="text-gray-500 dark:text-zinc-400 font-semibold mb-1">수락 기준</p>
                                    <ul className="space-y-0.5">
                                      {task.acceptance_criteria.map((c, i) => (
                                        <li key={i} className="flex gap-1.5 text-gray-700 dark:text-zinc-300">
                                          <span className="text-gray-400 dark:text-zinc-500 flex-shrink-0">·</span>
                                          <span>{c}</span>
                                        </li>
                                      ))}
                                    </ul>
                                  </div>
                                )}
                                {/* depends_on */}
                                {task.depends_on?.length > 0 && (
                                  <div>
                                    <p className="text-gray-500 dark:text-zinc-400 font-semibold mb-1">의존성</p>
                                    <div className="flex flex-wrap gap-1">
                                      {task.depends_on.map(dep => (
                                        <span key={dep} className="font-mono bg-gray-100 dark:bg-zinc-800 text-gray-600 dark:text-zinc-400 px-1.5 py-0.5 rounded">
                                          {dep}
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                )}
                                {/* failure_reason */}
                                {task.failure_reason && (
                                  <div>
                                    <p className="text-red-500 dark:text-red-400 font-semibold mb-0.5">실패 원인</p>
                                    {task.failure_reason.startsWith('[MAX_ITER]') && (
                                      <p className="text-orange-600 dark:text-orange-400 text-xs font-semibold mb-1">
                                        ⚠️ 에이전트가 최대 반복 횟수를 초과했습니다. 태스크를 더 작게 분할하세요.
                                      </p>
                                    )}
                                    <p className="text-red-600 dark:text-red-400 leading-relaxed whitespace-pre-wrap">
                                      {task.failure_reason.replace(/^\[MAX_ITER\]\s*/, '')}
                                    </p>
                                  </div>
                                )}
                                {/* report 상세 */}
                                {task.report && (
                                  <div className="flex flex-wrap gap-4 pt-1 border-t border-gray-200 dark:border-zinc-700">
                                    <span className="text-gray-500 dark:text-zinc-400">테스트 <strong className="text-gray-700 dark:text-zinc-300">{task.report.test_count}</strong></span>
                                    <span className="text-gray-500 dark:text-zinc-400">재시도 <strong className="text-gray-700 dark:text-zinc-300">{task.report.retry_count}</strong></span>
                                    {task.report.completed_at && (
                                      <span className="text-gray-500 dark:text-zinc-400">완료 <strong className="text-gray-700 dark:text-zinc-300">{task.report.completed_at}</strong></span>
                                    )}
                                  </div>
                                )}
                                {/* 실패 태스크 액션 */}
                                {task.status === 'failed' && (
                                  <div className="flex flex-wrap gap-2 pt-2 border-t border-gray-200 dark:border-zinc-700">
                                    <button
                                      onClick={() => {
                                        setEditDraft({ description: task.description ?? '', criteria: [...(task.acceptance_criteria ?? [])] })
                                        setEditingTaskId(task.id)
                                      }}
                                      className="rounded px-3 py-1 bg-gray-200 dark:bg-zinc-700 text-gray-700 dark:text-zinc-300 hover:bg-gray-300 dark:hover:bg-zinc-600 transition-colors"
                                    >✏️ 수정</button>
                                    {project && (
                                      <button
                                        onClick={() => redesignTask(task.id)}
                                        disabled={redesigningTaskIds.has(task.id)}
                                        className="rounded px-3 py-1 bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 transition-colors"
                                      >{redesigningTaskIds.has(task.id) ? 'AI 분석 중…' : '🤖 AI 재설계'}</button>
                                    )}
                                    {project && (
                                      <button
                                        onClick={() => rerunTask(task.id)}
                                        disabled={rerunningTaskId === task.id}
                                        className="rounded px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                                      >{rerunningTaskId === task.id ? '시작 중…' : '▶ 재실행'}</button>
                                    )}
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* 컨텍스트 문서 */}
              <div>
                <h2 className="text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-3">컨텍스트 문서</h2>
                {contextDocs.length === 0 ? (
                  <p className="text-sm text-gray-400 dark:text-zinc-500">
                    컨텍스트 문서가 없습니다.
                    <span className="block text-xs mt-0.5 font-mono">{repoPathForContext}/agent-data/context/</span>
                  </p>
                ) : (
                  <div className="border border-gray-200 dark:border-zinc-700 rounded-xl overflow-hidden divide-y divide-gray-100 dark:divide-zinc-800">
                    {contextDocs.map(doc => (
                      <button
                        key={doc.name}
                        onClick={() => openContextDoc(doc.name)}
                        className={`w-full text-left flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-zinc-800/50 transition-colors ${
                          selectedContextDoc?.name === doc.name ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-gray-400 dark:text-zinc-500 flex-shrink-0">
                            <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                              <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
                            </svg>
                          </span>
                          <span className="text-sm text-gray-800 dark:text-zinc-200 font-mono truncate">{doc.name}</span>
                        </div>
                        <span className="text-xs text-gray-400 dark:text-zinc-500 flex-shrink-0 ml-2">
                          {doc.size < 1024 ? `${doc.size}B` : `${(doc.size / 1024).toFixed(1)}KB`}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* 마일스톤 보고서 목록 */}
              <div>
                <h2 className="text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-3">마일스톤 보고서</h2>
                <div className="space-y-2">
                  {milestones.length === 0 ? (
                    <p className="text-sm text-gray-400 dark:text-zinc-500">마일스톤 보고서가 없습니다.</p>
                  ) : (
                    milestones.map(ms => (
                      <button
                        key={ms.filename}
                        onClick={() => openMilestone(ms.filename)}
                        className="w-full text-left flex items-center justify-between px-4 py-3 rounded-xl border border-gray-200 dark:border-zinc-700 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
                      >
                        <span className="text-sm text-gray-800 dark:text-zinc-200 font-medium">{ms.created_at}</span>
                        <span className="text-xs text-blue-500">열기 →</span>
                      </button>
                    ))
                  )}
                </div>
              </div>

              {/* 주간 보고서 */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-gray-700 dark:text-zinc-300">주간 보고서</h2>
                  <button
                    onClick={generateWeeklyReport}
                    disabled={generatingWeekly}
                    className="text-xs px-3 py-1.5 rounded-lg border border-blue-300 dark:border-blue-700 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
                  >
                    {generatingWeekly ? (
                      <>
                        <span className="inline-block w-3 h-3 border border-blue-500 border-t-transparent rounded-full animate-spin" />
                        생성 중…
                      </>
                    ) : '+ 이번 주 생성'}
                  </button>
                </div>
                <div className="space-y-2">
                  {weeklyReports.length === 0 ? (
                    <p className="text-sm text-gray-400 dark:text-zinc-500">주간 보고서가 없습니다.</p>
                  ) : (
                    weeklyReports.map(r => (
                      <button
                        key={`${r.year}-W${r.week}`}
                        onClick={() => openWeeklyReport(r.year, r.week)}
                        className={`w-full text-left flex items-center justify-between px-4 py-3 rounded-xl border transition-colors ${
                          selectedWeekly?.year === r.year && selectedWeekly?.week === r.week
                            ? 'border-blue-400 dark:border-blue-600 bg-blue-50 dark:bg-blue-900/20'
                            : 'border-gray-200 dark:border-zinc-700 hover:bg-gray-50 dark:hover:bg-zinc-800'
                        }`}
                      >
                        <span className="text-sm text-gray-800 dark:text-zinc-200 font-medium">
                          {r.year}년 {r.week}주차
                        </span>
                        <span className="text-xs text-blue-500">열기 →</span>
                      </button>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* 우측 패널: 마일스톤 / 주간 보고서 / 컨텍스트 문서 뷰어 */}
        {(selectedMilestone || selectedWeekly || selectedContextDoc) && (
          <div className="w-96 border-l border-gray-200 dark:border-zinc-700 flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-zinc-700 flex-shrink-0">
              <span className="text-sm font-semibold text-gray-700 dark:text-zinc-300 truncate">
                {selectedMilestone
                  ? selectedMilestone.filename
                  : selectedWeekly
                  ? `${selectedWeekly.year}년 ${selectedWeekly.week}주차 보고서`
                  : selectedContextDoc!.name}
              </span>
              <button
                onClick={() => { setSelectedMilestone(null); setSelectedWeekly(null); setSelectedContextDoc(null) }}
                className="text-gray-400 hover:text-gray-600 dark:text-zinc-500 dark:hover:text-zinc-300 ml-2 flex-shrink-0"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <pre className="text-xs text-gray-700 dark:text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                {selectedMilestone
                  ? selectedMilestone.content
                  : selectedWeekly
                  ? selectedWeekly.content
                  : selectedContextDoc!.content}
              </pre>
            </div>
          </div>
        )}
      </div>

      {showResumeModal && availableModels.length > 0 && (
        <PipelineModelModal
          models={availableModels}
          tasks={tasks.map(t => ({ id: t.id, complexity: t.complexity ?? null }))}
          onConfirm={resumePipeline}
          onCancel={() => setShowResumeModal(false)}
        />
      )}

      {/* 의존성 그래프 모달 */}
      {showGraphModal && (
        <DependencyGraphModal
          tasks={fullTasksForGraph.map(t => ({
            id: t.id as string,
            title: (t.title as string) ?? '',
            description: (t.description as string) ?? '',
            acceptance_criteria: (t.acceptance_criteria as string[]) ?? [],
            target_files: (t.target_files as string[]) ?? [],
            depends_on: (t.depends_on as string[]) ?? [],
            task_type: ((t.task_type as string) ?? 'backend') as 'backend' | 'frontend',
          }))}
          onClose={() => setShowGraphModal(false)}
          onApply={applyDependencyFix}
        />
      )}

      {/* AI 재설계 결과 모달 */}
      {redesignResults.length > 0 && (() => {
        const redesignResult = redesignResults[0]
        return (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="bg-white dark:bg-zinc-900 rounded-2xl shadow-2xl w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col">
              <div className="px-6 py-4 border-b border-gray-200 dark:border-zinc-700 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-gray-900 dark:text-zinc-100">
                    🤖 AI 재설계 제안
                  </h2>
                  <p className="text-xs text-gray-500 dark:text-zinc-400 mt-0.5">
                    {redesignResult.action === 'split' ? `태스크 ${redesignResult.tasks.length}개로 분할` : '태스크 단순화'} 제안
                    {redesignResults.length > 1 && <span className="ml-2 text-purple-500">({redesignResults.length}개 대기 중)</span>}
                  </p>
                </div>
                <button
                  onClick={() => setRedesignResults(prev => prev.slice(1))}
                  className="text-gray-400 hover:text-gray-600 dark:hover:text-zinc-200 text-xl leading-none"
                >✕</button>
              </div>
              <div className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
                <div className="bg-purple-50 dark:bg-purple-900/20 rounded-lg px-4 py-3 text-sm text-purple-900 dark:text-purple-200">
                  {redesignResult.explanation}
                </div>
                {redesignResult.tasks.map((t, i) => (
                  <div key={i} className="border border-gray-200 dark:border-zinc-700 rounded-lg p-4 space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-gray-500 dark:text-zinc-400">{String(t.id)}</span>
                      <span className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{String(t.title)}</span>
                    </div>
                    <p className="text-xs text-gray-600 dark:text-zinc-300 leading-relaxed">{String(t.description)}</p>
                    {Array.isArray(t.acceptance_criteria) && (
                      <ul className="text-xs text-gray-500 dark:text-zinc-400 space-y-0.5 list-disc list-inside">
                        {(t.acceptance_criteria as string[]).map((c, j) => <li key={j}>{c}</li>)}
                      </ul>
                    )}
                    {Array.isArray(t.depends_on) && (t.depends_on as string[]).length > 0 && (
                      <p className="text-xs text-gray-400 dark:text-zinc-500">depends_on: {(t.depends_on as string[]).join(', ')}</p>
                    )}
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-gray-200 dark:border-zinc-700 flex gap-2 justify-end">
                <button
                  onClick={() => setRedesignResults(prev => prev.slice(1))}
                  className="rounded-lg px-4 py-2 text-sm bg-gray-200 dark:bg-zinc-700 text-gray-700 dark:text-zinc-300 hover:bg-gray-300 dark:hover:bg-zinc-600 transition-colors"
                >취소</button>
                <button
                  onClick={applyRedesign}
                  disabled={applyingRedesign}
                  className="rounded-lg px-4 py-2 text-sm bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 transition-colors"
                >{applyingRedesign ? '적용 중…' : '✓ 적용 및 pending으로 초기화'}</button>
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
