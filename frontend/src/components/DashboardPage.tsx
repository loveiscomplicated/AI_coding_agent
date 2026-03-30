import { useEffect, useState } from 'react'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

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

// ── 헬퍼 컴포넌트 ─────────────────────────────────────────────────────────────

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

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────

export function DashboardPage() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [tasks, setTasks] = useState<DashboardTask[]>([])
  const [milestones, setMilestones] = useState<Milestone[]>([])
  const [selectedMilestone, setSelectedMilestone] = useState<{ filename: string; content: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      try {
        const [summaryRes, tasksRes, milestonesRes] = await Promise.all([
          fetch(`${API_BASE}/api/dashboard/summary`),
          fetch(`${API_BASE}/api/dashboard/tasks`),
          fetch(`${API_BASE}/api/dashboard/milestones`),
        ])
        if (!summaryRes.ok || !tasksRes.ok || !milestonesRes.ok) {
          throw new Error('API 응답 오류')
        }
        const [s, t, m] = await Promise.all([summaryRes.json(), tasksRes.json(), milestonesRes.json()])
        setSummary(s)
        setTasks(t.tasks ?? [])
        setMilestones(m.milestones ?? [])
      } catch (e) {
        setError('대시보드 데이터를 불러오지 못했습니다.')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const openMilestone = async (filename: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/dashboard/milestones/${filename}`)
      const data = await res.json()
      setSelectedMilestone({ filename, content: data.content })
    } catch {
      // ignore
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 dark:text-zinc-500">
        <div className="w-5 h-5 border-2 border-gray-300 dark:border-zinc-600 border-t-blue-500 rounded-full animate-spin mr-2" />
        불러오는 중...
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-500 text-sm">{error}</div>
    )
  }

  const m = summary?.metrics

  return (
    <div className="flex h-full overflow-hidden">
      {/* 메인 대시보드 */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* 헤더 */}
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-zinc-100">대시보드</h1>
          <p className="text-sm text-gray-500 dark:text-zinc-400 mt-0.5">파이프라인 실행 현황 및 메트릭</p>
        </div>

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
            <MetricCard
              label="총 재시도 횟수"
              value={m.total_retries}
            />
            <MetricCard
              label="마일스톤 보고서"
              value={summary?.milestone_count ?? 0}
            />
            <MetricCard
              label="태스크 상태"
              value={Object.entries(summary?.task_status ?? {}).map(([k, v]) => `${k}: ${v}`).join(' / ') || '—'}
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
  )
}
