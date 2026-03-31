/**
 * ProjectListPage.tsx
 *
 * 대시보드 진입점 — 등록된 프로젝트 카드 목록을 보여준다.
 * 카드 클릭 → 프로젝트 상세 (DashboardPage)
 */

import { useEffect, useRef, useState } from 'react'
import { Project, loadProjects, saveProjects, projectTasksPath } from '../storage/projectStorage'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

export type { Project }

// ── 타입 ──────────────────────────────────────────────────────────────────────

type ProjectStatus = 'running' | 'paused' | 'failed' | 'idle'

interface JobSummary {
  job_id: string
  status: string
  paused?: boolean
  result?: { success: number; fail: number } | null
  request?: { tasks_path: string; repo_path: string; reports_dir?: string; base_branch?: string }
}

// ── 경로 유사 여부 ─────────────────────────────────────────────────────────────

function pathsOverlap(a: string, b: string): boolean {
  if (!a || !b) return false
  return a === b || a.endsWith('/' + b) || b.endsWith('/' + a)
}

// ── 상태 판단 ─────────────────────────────────────────────────────────────────

function resolveStatus(project: Project, jobs: JobSummary[]): ProjectStatus {
  // repo_path 기준으로 이 프로젝트의 잡 필터
  const mine = jobs.filter(j => pathsOverlap(project.rootDir, j.request?.repo_path ?? ''))
  if (mine.length === 0) return 'idle'

  const running = mine.find(j => j.status === 'running')
  if (running) return running.paused ? 'paused' : 'running'

  const last = mine[0]
  if (last.status === 'error') return 'failed'
  if (last.result && last.result.fail > 0) return 'failed'
  return 'idle'
}

function StatusBadge({ status }: { status: ProjectStatus }) {
  if (status === 'idle') return null
  const colorMap: Record<string, string> = {
    running: 'text-green-500',
    paused:  'text-amber-400',
    failed:  'text-red-500',
  }
  const labelMap: Record<string, string> = {
    running: '● 진행중',
    paused:  '⏸ 일시정지',
    failed:  '● 실패',
  }
  return (
    <span className={`text-xs font-medium ${colorMap[status]}`}>
      {labelMap[status]}
    </span>
  )
}

// ── 새 프로젝트 모달 ──────────────────────────────────────────────────────────

interface NewProjectModalProps {
  onClose: () => void
  onCreate: (p: Project) => void
}

function NewProjectModal({ onClose, onCreate }: NewProjectModalProps) {
  const [name, setName] = useState('')
  const [rootDir, setRootDir] = useState('')
  const [baseBranch, setBaseBranch] = useState('main')
  const [browsing, setBrowsing] = useState(false)

  async function browse() {
    setBrowsing(true)
    try {
      const initial = rootDir || '~'
      const res = await fetch(`${API_BASE}/api/utils/browse?type=folder&initial=${encodeURIComponent(initial)}`)
      const data = await res.json()
      if (!data.cancelled && data.path) {
        setRootDir(data.path)
        // 이름이 비어 있으면 폴더명 자동 입력
        if (!name.trim()) {
          setName(data.path.replace(/\/+$/, '').split('/').pop() ?? '')
        }
      }
    } finally {
      setBrowsing(false)
    }
  }

  function handleCreate() {
    if (!name.trim() || !rootDir.trim()) return
    onCreate({
      id: crypto.randomUUID(),
      name: name.trim(),
      rootDir: rootDir.replace(/\/+$/, ''),
      baseBranch: baseBranch.trim() || 'main',
      createdAt: new Date().toISOString(),
    })
  }

  const derived = rootDir ? projectTasksPath({ rootDir }) : null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-zinc-900 rounded-2xl shadow-2xl w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-bold text-gray-900 dark:text-zinc-100">새 프로젝트</h2>

        {/* 루트 디렉토리 */}
        <div>
          <label className="text-xs text-gray-500 dark:text-zinc-400 mb-1 block">프로젝트 루트 디렉토리</label>
          <div className="flex gap-1">
            <input
              autoFocus
              className="flex-1 text-xs rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-2 bg-white dark:bg-zinc-800 text-gray-700 dark:text-zinc-300 outline-none focus:border-blue-500"
              value={rootDir}
              onChange={e => setRootDir(e.target.value)}
              placeholder="/path/to/project"
              onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
            />
            <button
              onClick={browse}
              disabled={browsing}
              className="px-2 py-1.5 rounded-lg border border-gray-300 dark:border-zinc-600 text-gray-500 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 disabled:opacity-40"
              title="파인더에서 폴더 선택"
            >
              {browsing
                ? <span className="inline-block w-3.5 h-3.5 border border-gray-400 border-t-transparent rounded-full animate-spin" />
                : <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor"><path d="M2 6a2 2 0 012-2h4l2 2h6a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"/></svg>
              }
            </button>
          </div>
          {derived && (
            <p className="text-[10px] text-gray-400 dark:text-zinc-500 mt-1 font-mono">
              → tasks: {derived}
            </p>
          )}
        </div>

        {/* 이름 + 브랜치 */}
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="text-xs text-gray-500 dark:text-zinc-400 mb-1 block">프로젝트 이름</label>
            <input
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-2 bg-white dark:bg-zinc-800 text-gray-800 dark:text-zinc-100 outline-none focus:border-blue-500"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
              placeholder="my_project"
            />
          </div>
          <div className="w-28">
            <label className="text-xs text-gray-500 dark:text-zinc-400 mb-1 block">기본 브랜치</label>
            <input
              className="w-full text-sm rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-2 bg-white dark:bg-zinc-800 text-gray-800 dark:text-zinc-100 outline-none focus:border-blue-500"
              value={baseBranch}
              onChange={e => setBaseBranch(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
              placeholder="main"
            />
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-zinc-600 text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800"
          >
            취소
          </button>
          <button
            onClick={handleCreate}
            disabled={!name.trim() || !rootDir.trim()}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            만들기
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────

interface Props {
  onSelectProject: (project: Project) => void
}

// ── 경로 유사 여부 판단 ───────────────────────────────────────────────────────

export function ProjectListPage({ onSelectProject }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<'name' | 'created'>('name')
  const [showNew, setShowNew] = useState(false)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [addedCount, setAddedCount] = useState<number | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setProjects(loadProjects())
    fetch(`${API_BASE}/api/pipeline/jobs`)
      .then(r => r.ok ? r.json() : { jobs: [] })
      .then(data => setJobs(data.jobs ?? []))
      .catch(() => {})
  }, [])

  // 외부 클릭 시 컨텍스트 메뉴 닫기
  useEffect(() => {
    if (!menuOpenId) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpenId(null)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpenId])

  async function refreshFromBackend() {
    setRefreshing(true)
    setAddedCount(null)
    try {
      const data = await fetch(`${API_BASE}/api/pipeline/jobs`).then(r => r.json())
      const fetchedJobs: JobSummary[] = data.jobs ?? []
      setJobs(fetchedJobs)

      setProjects(prev => {
        const seen = new Set<string>()
        const toAdd: Project[] = []

        for (const job of fetchedJobs) {
          const repoPath = job.request?.repo_path
          if (!repoPath) continue

          if (seen.has(repoPath)) continue
          seen.add(repoPath)

          // 이미 등록된 프로젝트인지 확인 (rootDir 기준)
          const exists = prev.some(p => pathsOverlap(p.rootDir, repoPath))
          if (exists) continue

          // repo_path 마지막 세그먼트를 이름으로
          const name = repoPath.replace(/\/+$/, '').split('/').pop() ?? repoPath

          toAdd.push({
            id: crypto.randomUUID(),
            name,
            rootDir: repoPath.replace(/\/+$/, ''),
            baseBranch: job.request?.base_branch ?? 'main',
            createdAt: new Date().toISOString(),
          })
        }

        if (toAdd.length === 0) {
          setAddedCount(0)
          return prev
        }
        const updated = [...prev, ...toAdd]
        saveProjects(updated)
        setAddedCount(toAdd.length)
        return updated
      })
    } catch { /* 백엔드 미기동 무시 */ }
    finally { setRefreshing(false) }
  }

  function handleCreate(p: Project) {
    const updated = [...projects, p]
    saveProjects(updated)
    setProjects(updated)
    setShowNew(false)
  }

  function handleDelete(id: string) {
    const updated = projects.filter(p => p.id !== id)
    saveProjects(updated)
    setProjects(updated)
    setMenuOpenId(null)
  }

  const filtered = projects
    .filter(p => !search || p.name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => sort === 'name'
      ? a.name.localeCompare(b.name)
      : b.createdAt.localeCompare(a.createdAt))

  return (
    <div className="flex flex-col h-full bg-white dark:bg-zinc-950 overflow-hidden">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-6 py-5 flex-shrink-0">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-zinc-100">프로젝트</h1>
        <div className="flex items-center gap-2">
          {/* 새로고침 버튼 */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={refreshFromBackend}
              disabled={refreshing}
              className="flex items-center gap-1.5 rounded-xl border border-gray-200 dark:border-zinc-700 px-3 py-2 text-sm text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 disabled:opacity-50 transition-colors"
              title="백엔드에서 실행된 프로젝트를 자동으로 가져옵니다"
            >
              <svg
                className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`}
                viewBox="0 0 20 20" fill="currentColor"
              >
                <path fillRule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clipRule="evenodd" />
              </svg>
              새로고침
            </button>
            {addedCount !== null && (
              <span className="text-xs text-gray-400 dark:text-zinc-500">
                {addedCount > 0 ? `+${addedCount}개 추가됨` : '새 프로젝트 없음'}
              </span>
            )}
          </div>
          <button
            onClick={() => setShowNew(true)}
            className="flex items-center gap-1.5 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
          >
            <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
            </svg>
            새 프로젝트
          </button>
        </div>
      </div>

      {/* 검색 */}
      <div className="px-6 pb-3 flex-shrink-0">
        <div className="flex items-center gap-3 rounded-xl border-2 border-blue-500 px-4 py-2.5 bg-white dark:bg-zinc-900">
          <svg className="w-4 h-4 text-gray-400 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
          <input
            className="flex-1 text-sm bg-transparent text-gray-700 dark:text-zinc-200 placeholder-gray-400 dark:placeholder-zinc-500 outline-none"
            placeholder="프로젝트 검색"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* 정렬 */}
      <div className="flex justify-end px-6 pb-3 flex-shrink-0">
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-zinc-400">
          <span>정렬 기준</span>
          <select
            value={sort}
            onChange={e => setSort(e.target.value as 'name' | 'created')}
            className="rounded-lg border border-gray-200 dark:border-zinc-700 px-2 py-1 bg-white dark:bg-zinc-900 text-gray-700 dark:text-zinc-200 text-xs outline-none"
          >
            <option value="name">이름</option>
            <option value="created">생성일</option>
          </select>
        </div>
      </div>

      {/* 카드 그리드 */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
            <p className="text-sm text-gray-400 dark:text-zinc-500">
              {search ? '검색 결과가 없습니다' : '프로젝트가 없습니다. 새 프로젝트를 만들거나 새로고침을 눌러보세요.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            {filtered.map(project => {
              const status = resolveStatus(project, jobs)
              return (
                <div
                  key={project.id}
                  onClick={() => onSelectProject(project)}
                  className="relative flex flex-col justify-between rounded-2xl border border-gray-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 cursor-pointer hover:border-blue-400 dark:hover:border-blue-500 hover:shadow-md transition-all min-h-[140px]"
                >
                  {/* 상단 */}
                  <div className="flex items-start justify-between">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-gray-800 dark:text-zinc-100 truncate">
                        {project.name}
                      </p>
                      <p className="text-[10px] text-gray-400 dark:text-zinc-500 font-mono truncate mt-0.5">
                        {project.rootDir}
                      </p>
                    </div>
                    {/* 컨텍스트 메뉴 */}
                    <div className="relative flex-shrink-0 ml-2" ref={menuOpenId === project.id ? menuRef : null}>
                      <button
                        onClick={e => { e.stopPropagation(); setMenuOpenId(menuOpenId === project.id ? null : project.id) }}
                        className="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 hover:bg-gray-100 dark:hover:bg-zinc-800"
                      >
                        <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                          <path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM16 12a2 2 0 100-4 2 2 0 000 4z" />
                        </svg>
                      </button>
                      {menuOpenId === project.id && (
                        <div className="absolute right-0 top-7 z-20 bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 rounded-lg shadow-xl py-1 w-28">
                          <button
                            onClick={e => { e.stopPropagation(); handleDelete(project.id) }}
                            className="w-full text-left px-3 py-2 text-xs text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
                          >
                            삭제
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* 하단 상태 배지 */}
                  <div className="mt-4">
                    <StatusBadge status={status} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {showNew && (
        <NewProjectModal
          onClose={() => setShowNew(false)}
          onCreate={handleCreate}
        />
      )}
    </div>
  )
}
