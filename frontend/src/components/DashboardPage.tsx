import { useEffect, useRef, useState } from 'react'

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

export function DashboardPage() {
  const [config, setConfig] = useState<ProjectConfig>(() => {
    const recent = loadRecent()
    return recent[0] ?? { reportsDir: 'data/reports', tasksPath: 'data/tasks.yaml' }
  })
  const [summary, setSummary] = useState<Summary | null>(null)
  const [tasks, setTasks] = useState<DashboardTask[]>([])
  const [milestones, setMilestones] = useState<Milestone[]>([])
  const [selectedMilestone, setSelectedMilestone] = useState<{ filename: string; content: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

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
      {/* 프로젝트 선택 바 */}
      <ProjectBar
        config={config}
        onChange={setConfig}
        onLoad={() => loadData(config)}
        loading={loading}
      />

      {/* 본문 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 메인 대시보드 */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* 헤더 */}
          <div>
            <h1 className="text-xl font-bold text-gray-900 dark:text-zinc-100">대시보드</h1>
            <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5 font-mono truncate">
              {config.reportsDir}
            </p>
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
                <div className="border border-gray-200 dark:border-zinc-700 rounded-xl overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 dark:bg-zinc-800">
                      <tr>
                        <th className="text-left px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-zinc-400">태스크</th>
                        <th className="text-left px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-zinc-400">상태</th>
                        <th className="text-left px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-zinc-400">리뷰</th>
                        <th className="text-right px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-zinc-400">테스트</th>
                        <th className="text-right px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-zinc-400">소요 시간</th>
                        <th className="text-right px-4 py-2.5 text-xs font-semibold text-gray-500 dark:text-zinc-400">재시도</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-zinc-800">
                      {tasks.map(task => (
                        <tr key={task.id} className="hover:bg-gray-50 dark:hover:bg-zinc-800/50 transition-colors">
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-gray-400 dark:text-zinc-500 font-mono">{task.id}</span>
                              <span className="text-gray-800 dark:text-zinc-200 font-medium truncate max-w-[180px]">{task.title}</span>
                            </div>
                            {task.pr_url && (
                              <a href={task.pr_url} target="_blank" rel="noopener noreferrer"
                                className="text-xs text-blue-500 hover:underline mt-0.5 block">PR →</a>
                            )}
                          </td>
                          <td className="px-4 py-3"><StatusBadge status={task.status} /></td>
                          <td className="px-4 py-3">
                            <VerdictBadge verdict={task.report?.reviewer_verdict ?? ''} />
                          </td>
                          <td className="px-4 py-3 text-right text-gray-600 dark:text-zinc-400">
                            {task.report ? task.report.test_count : '—'}
                          </td>
                          <td className="px-4 py-3 text-right text-gray-600 dark:text-zinc-400">
                            {task.report ? `${task.report.time_elapsed_seconds}s` : '—'}
                          </td>
                          <td className="px-4 py-3 text-right text-gray-600 dark:text-zinc-400">
                            {task.report ? task.report.retry_count : '—'}
                          </td>
                        </tr>
                      ))}
                      {tasks.length === 0 && (
                        <tr>
                          <td colSpan={6} className="px-4 py-8 text-center text-gray-400 dark:text-zinc-500 text-sm">
                            태스크가 없습니다
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
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
