import { useEffect, useRef, useState, useCallback } from 'react'
import { projectTasksPath, projectReportsDir } from '../storage/projectStorage'
import { ACTIVE_JOB_KEY } from './PipelineLogView'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string
const RECENT_KEY = 'dashboard_recent_projects'
const MAX_RECENT = 5

// ── 타입 ─────────────────────────────────────────────────────────────────────

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
  }
  milestone_count: number
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
  report: {
    test_count: number
    retry_count: number
    reviewer_verdict: string
    time_elapsed_seconds: number
    completed_at: string
  } | null
}

interface Milestone {
  filename: string
  path: string
  created_at: string
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

function VerdictBadge({ verdict }: { verdict: string }) {
  if (!verdict) return null
  const approved = verdict === 'APPROVED'
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
          placeholder="/path/to/project/data/reports"
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
          placeholder="data/tasks.yaml"
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

export function DashboardPage({ project, onBack, onPipelineStarted, onDiscordChannelCreated }: DashboardPageProps = {}) {
  const [config, setConfig] = useState<ProjectConfig>(() => {
    if (project) {
      return {
        reportsDir: projectReportsDir(project),
        tasksPath: projectTasksPath(project),
      }
    }
    const recent = loadRecent()
    return recent[0] ?? { reportsDir: 'data/reports', tasksPath: 'data/tasks.yaml' }
  })
  const [summary, setSummary] = useState<Summary | null>(null)
  const [tasks, setTasks] = useState<DashboardTask[]>([])
  const [milestones, setMilestones] = useState<Milestone[]>([])
  const [selectedMilestone, setSelectedMilestone] = useState<{ filename: string; content: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeJob, setActiveJob] = useState<ActiveJob | null>(null)
  const [controlling, setControlling] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null)
  const [autoMerge, setAutoMerge] = useState<boolean>(() => {
    return localStorage.getItem('pipeline_auto_merge') === 'true'
  })

  const loadData = async (cfg: ProjectConfig) => {
    setLoading(true)
    setError('')
    setSelectedMilestone(null)
    const q = buildQuery(cfg)
    try {
      const [summaryRes, tasksRes, milestonesRes] = await Promise.all([
        fetch(`${API_BASE}/api/dashboard/summary?${q}`),
        fetch(`${API_BASE}/api/dashboard/tasks?${q}`),
        fetch(`${API_BASE}/api/dashboard/milestones?${q}`),
      ])
      if (!summaryRes.ok || !tasksRes.ok || !milestonesRes.ok) {
        throw new Error('API 응답 오류')
      }
      const [s, t, m] = await Promise.all([summaryRes.json(), tasksRes.json(), milestonesRes.json()])
      setSummary(s)
      setTasks(t.tasks ?? [])
      setMilestones(m.milestones ?? [])
      saveRecent(cfg)
    } catch {
      setError('대시보드 데이터를 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData(config) }, [])

  // 프로젝트 매칭 잡 조회 (파이프라인 제어용)
  const fetchActiveJob = useCallback(async () => {
    if (!project) return
    try {
      const data = await fetch(`${API_BASE}/api/pipeline/jobs`).then(r => r.json())
      const jobs = data.jobs ?? []
      const matched = jobs.find((j: any) =>
        pathsOverlap(project.rootDir, j.request?.repo_path ?? '')
      )
      // running 상태인 잡만 제어 대상
      setActiveJob(matched && matched.status === 'running' ? matched : null)
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
        // 파이프라인이 실제 종료될 때까지 시간이 걸리므로 즉시 버튼 숨김.
        // 폴링이 done 상태를 감지하면 자연스럽게 사라짐.
        setActiveJob(null)
      } else {
        await fetchActiveJob()
      }
    } finally {
      setControlling(false)
    }
  }

  async function resumePipeline() {
    if (!project) return
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
          discord_channel_id: project.discordChannelId ?? null,
          auto_merge: autoMerge,
        }),
      })
      if (!res.ok) throw new Error('파이프라인 시작 실패')
      const data = await res.json()
      // 새로 생성된 Discord 채널 ID를 Project에 저장
      if (data.discord_channel_id && data.discord_channel_id !== project.discordChannelId) {
        onDiscordChannelCreated?.(data.discord_channel_id)
      }
      localStorage.setItem(ACTIVE_JOB_KEY, data.job_id)
      onPipelineStarted?.(data.job_id)
    } catch (e) {
      alert(e instanceof Error ? e.message : '오류 발생')
    } finally {
      setResuming(false)
    }
  }

  const openMilestone = async (filename: string) => {
    try {
      const q = buildQuery(config)
      const res = await fetch(`${API_BASE}/api/dashboard/milestones/${filename}?${q}`)
      const data = await res.json()
      setSelectedMilestone({ filename, content: data.content })
    } catch { /* ignore */ }
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
                    onClick={resumePipeline}
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
                    {activeJob.paused ? (
                      <span className="text-xs text-amber-400 font-medium">⏸ 일시정지됨</span>
                    ) : (
                      <span className="flex items-center gap-1 text-xs text-green-500 font-medium">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse inline-block" />
                        실행 중
                      </span>
                    )}
                  </div>
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
                  >
                    ■ 중단
                  </button>
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
                    label="태스크 상태"
                    value={
                      Object.entries(summary?.task_status ?? {})
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(' / ') || '—'
                    }
                  />
                </div>
              )}

              {/* 태스크 목록 */}
              <div>
                <h2 className="text-sm font-semibold text-gray-700 dark:text-zinc-300 mb-3">태스크 목록</h2>
                <div className="border border-gray-200 dark:border-zinc-700 rounded-xl overflow-hidden divide-y divide-gray-100 dark:divide-zinc-800">
                  {tasks.length === 0 ? (
                    <div className="px-4 py-8 text-center text-gray-400 dark:text-zinc-500 text-sm">
                      태스크가 없습니다
                    </div>
                  ) : tasks.map(task => {
                    const expanded = expandedTaskId === task.id
                    return (
                      <div key={task.id}>
                        {/* 요약 행 */}
                        <button
                          onClick={() => setExpandedTaskId(expanded ? null : task.id)}
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
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
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
            </>
          )}
        </div>

        {/* 마일스톤 보고서 뷰어 */}
        {selectedMilestone && (
          <div className="w-96 border-l border-gray-200 dark:border-zinc-700 flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-zinc-700 flex-shrink-0">
              <span className="text-sm font-semibold text-gray-700 dark:text-zinc-300 truncate">
                {selectedMilestone.filename}
              </span>
              <button
                onClick={() => setSelectedMilestone(null)}
                className="text-gray-400 hover:text-gray-600 dark:text-zinc-500 dark:hover:text-zinc-300 ml-2 flex-shrink-0"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <pre className="text-xs text-gray-700 dark:text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                {selectedMilestone.content}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
