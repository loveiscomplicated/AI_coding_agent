import { useState, useEffect, useRef } from 'react'
import { MeetingRecord } from './types/meeting'
import { MeetingStorage } from './storage/meetingStorage'
import { MeetingApp } from './components/MeetingApp'
import { ChatListPage } from './components/ChatListPage'
import { DashboardPage } from './components/DashboardPage'
import { PipelineLogView, ACTIVE_JOB_KEY } from './components/PipelineLogView'
import { PipelineTaskTracker } from './components/PipelineTaskTracker'
import { ProjectListPage } from './components/ProjectListPage'
import { Project, loadProjects, saveProjects } from './storage/projectStorage'

const storage = new MeetingStorage()
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

// ── 파이프라인 탭 래퍼 ────────────────────────────────────────────────────────

type PipelineTab = 'tracker' | 'log'

function PipelineView({ jobId, onDone }: { jobId: string; onDone: () => void }) {
  const [tab, setTab] = useState<PipelineTab>('tracker')
  return (
    <div className="flex flex-col h-full">
      {/* 탭 헤더 */}
      <div className="flex items-center gap-0.5 px-3 pt-2 pb-0 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700 flex-shrink-0">
        <button
          onClick={() => setTab('tracker')}
          className={`px-3 py-1.5 text-xs font-medium rounded-t transition-colors ${
            tab === 'tracker'
              ? 'bg-zinc-800 text-zinc-100 border border-b-zinc-800 border-zinc-700'
              : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          라이브 뷰
        </button>
        <button
          onClick={() => setTab('log')}
          className={`px-3 py-1.5 text-xs font-medium rounded-t transition-colors ${
            tab === 'log'
              ? 'bg-zinc-800 text-zinc-100 border border-b-zinc-800 border-zinc-700'
              : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          로그
        </button>
      </div>
      <div className="flex-1 min-h-0">
        {tab === 'tracker'
          ? <PipelineTaskTracker key={jobId} jobId={jobId} />
          : <PipelineLogView key={jobId} jobId={jobId} onDone={onDone} />
        }
      </div>
    </div>
  )
}

function SidebarToggleIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 3v18" />
    </svg>
  )
}

export default function App() {
  const [backendOk, setBackendOk] = useState<boolean | null>(null)
  const checkedRef = useRef(false)

  useEffect(() => {
    if (checkedRef.current) return
    checkedRef.current = true
    fetch(`${API_BASE}/api/health`)
      .then((r) => setBackendOk(r.ok))
      .catch(() => setBackendOk(false))
  }, [])

  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [records, setRecords] = useState<MeetingRecord[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [chatKey, setChatKey] = useState(0)
  const [search, setSearch] = useState('')
  const [showListPage, setShowListPage] = useState(false)
  const [showDashboard, setShowDashboard] = useState(false)
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)
  const [runningJobIds, setRunningJobIds] = useState<string[]>([])
  const [viewingJobId, setViewingJobId] = useState<string | null>(null)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  // 새 회의 타입 선택
  const [newMeetingMenu, setNewMeetingMenu] = useState(false)
  const [activeMeetingType, setActiveMeetingType] = useState<'project' | 'system'>('project')
  const [activeExecutionBrief, setActiveExecutionBrief] = useState<string>('')
  const [loadingBrief, setLoadingBrief] = useState(false)
  const [darkMode, setDarkMode] = useState(() => {
    const saved = localStorage.getItem('darkMode')
    return saved ? saved === 'true' : window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
    localStorage.setItem('darkMode', String(darkMode))
  }, [darkMode])

  useEffect(() => {
    setRecords(storage.list())
  }, [])

  // 새로고침 후 실행 중이던 파이프라인 잡 복원 (복수 지원)
  useEffect(() => {
    const raw = localStorage.getItem(ACTIVE_JOB_KEY)
    if (!raw) return
    // 구 형식(단일 문자열)과 신 형식(JSON 배열) 모두 처리
    let savedIds: string[]
    try {
      const parsed = JSON.parse(raw)
      savedIds = Array.isArray(parsed) ? parsed : [parsed]
    } catch {
      savedIds = [raw]
    }
    Promise.all(
      savedIds.map(id =>
        fetch(`${API_BASE}/api/pipeline/status/${id}`)
          .then(r => r.ok ? r.json() : null)
          .catch(() => null)
      )
    ).then(results => {
      const still = savedIds.filter((_, i) => results[i]?.status === 'running')
      if (still.length > 0) {
        setRunningJobIds(still)
        localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(still))
      } else {
        localStorage.removeItem(ACTIVE_JOB_KEY)
      }
    })
  }, [])

  const activeRecord = records.find(r => r.id === activeId)
  const filtered = records.filter(r =>
    !search || r.title.toLowerCase().includes(search.toLowerCase())
  )

  const startNew = (type: 'project' | 'system' = 'project', brief = '') => {
    setNewMeetingMenu(false)
    setShowListPage(false)
    setShowDashboard(false)
    setSelectedProject(null)
    setActiveId(null)
    setActiveMeetingType(type)
    setActiveExecutionBrief(brief)
    setChatKey(k => k + 1)
  }

  const startSystemMeeting = async () => {
    setNewMeetingMenu(false)
    setLoadingBrief(true)
    try {
      const res = await fetch(`${API_BASE}/api/execution-brief`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const data = res.ok ? await res.json() : { brief: '' }
      startNew('system', data.brief ?? '')
    } catch {
      startNew('system', '')
    } finally {
      setLoadingBrief(false)
    }
  }

  const handleSelect = (record: MeetingRecord) => {
    setShowListPage(false)
    setShowDashboard(false)
    setActiveId(record.id)
    setActiveMeetingType(record.meetingType ?? 'project')
    setActiveExecutionBrief('')
    setChatKey(k => k + 1)
  }

  const handleDelete = (id: string) => {
    storage.delete(id)
    setRecords(storage.list())
    if (activeId === id) {
      setActiveId(null)
      setChatKey(k => k + 1)
    }
  }

  const handleRename = (id: string, newTitle: string) => {
    const record = records.find(r => r.id === id)
    if (!record) return
    const trimmed = newTitle.trim()
    if (trimmed) {
      storage.save({ ...record, title: trimmed, updatedAt: new Date().toISOString() })
      setRecords(storage.list())
    }
    setRenamingId(null)
  }

  // 메뉴 외부 클릭 시 닫기
  useEffect(() => {
    if (!menuOpenId && !newMeetingMenu) return
    const close = () => { setMenuOpenId(null); setNewMeetingMenu(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpenId, newMeetingMenu])

  const handleFinished = (record: MeetingRecord) => {
    setRecords(storage.list())
    setActiveId(record.id)
  }

  if (backendOk === false) {
    return (
      <div className="flex items-center justify-center h-screen text-center p-8">
        <div>
          <p className="text-lg font-bold text-red-600 mb-2">백엔드 서버에 연결할 수 없습니다</p>
          <p className="text-sm text-gray-500 mb-1">
            아래 명령어로 백엔드를 먼저 실행하세요:
          </p>
          <code className="text-sm bg-gray-100 px-2 py-1 rounded">
            uvicorn backend.main:app --reload --port 8000
          </code>
        </div>
      </div>
    )
  }

  const sidebarToggle = (
    <button
      onClick={() => setSidebarOpen(o => !o)}
      className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors flex-shrink-0"
      title={sidebarOpen ? '사이드바 닫기' : '사이드바 열기'}
    >
      <SidebarToggleIcon />
    </button>
  )

  const mainToggle = (
    <button
      onClick={() => setSidebarOpen(true)}
      className="p-1.5 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
      title="사이드바 열기"
    >
      <SidebarToggleIcon />
    </button>
  )

  return (
    <div className="flex h-screen overflow-hidden bg-white dark:bg-zinc-950">
      {/* ── 사이드바 ── */}
      <aside
        className={`${
          sidebarOpen ? 'w-64' : 'w-0'
        } transition-[width] duration-300 ease-in-out flex-shrink-0 overflow-hidden bg-zinc-900 flex flex-col`}
      >
        {/* 헤더 */}
        <div className="flex items-center px-2 py-2.5 gap-1 flex-shrink-0">
          {sidebarToggle}
          <div className="flex-1" />
          {/* 새 회의 버튼 + 타입 드롭다운 */}
          <div className="relative">
            <button
              onClick={() => setNewMeetingMenu(m => !m)}
              className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors"
              title="새 회의"
              disabled={loadingBrief}
            >
              {loadingBrief ? (
                <div className="w-4 h-4 border border-zinc-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              )}
            </button>
            {newMeetingMenu && (
              <div
                onMouseDown={e => e.stopPropagation()}
                className="absolute right-0 top-8 z-30 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl py-1 w-40"
              >
                <button
                  onClick={() => startNew('project')}
                  className="w-full text-left px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700 transition-colors flex items-center gap-2"
                >
                  <span>🏗️</span> 프로젝트 회의
                </button>
                <button
                  onClick={startSystemMeeting}
                  className="w-full text-left px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700 transition-colors flex items-center gap-2"
                >
                  <span>⚙️</span> 시스템 회의
                </button>
              </div>
            )}
          </div>
        </div>

        {/* 검색 */}
        <div className="px-2 pb-2 flex-shrink-0">
          <div className="flex items-center gap-2 bg-zinc-800 rounded-lg px-3 py-1.5">
            <svg className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8" />
              <path d="m21 21-4.35-4.35" />
            </svg>
            <input
              type="text"
              placeholder="검색"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="bg-transparent text-sm text-zinc-100 placeholder-zinc-500 outline-none flex-1 min-w-0"
            />
            {search && (
              <button onClick={() => setSearch('')} className="text-zinc-500 hover:text-zinc-300 flex-shrink-0">
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* 실행 중인 파이프라인 배지 (복수 지원) */}
        {runningJobIds.length > 0 && (
          <div className="px-2 pb-1 flex-shrink-0 space-y-1">
            {runningJobIds.map(jobId => (
              <button
                key={jobId}
                onClick={() => {
                  setViewingJobId(jobId)
                  setShowListPage(false)
                  setShowDashboard(false)
                  setActiveId(null)
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors text-xs ${
                  viewingJobId === jobId
                    ? 'bg-green-800/60 text-green-300'
                    : 'bg-green-900/40 text-green-400 hover:bg-green-900/60'
                }`}
              >
                <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" />
                <span className="truncate">파이프라인 {jobId.slice(0, 8)} 실행 중</span>
              </button>
            ))}
          </div>
        )}

        {/* 네비게이션 링크 */}
        <div className="px-2 pb-1 flex-shrink-0 space-y-0.5">
          <button
            onClick={() => { setRecords(storage.list()); setShowListPage(true); setShowDashboard(false) }}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
              showListPage
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
            }`}
          >
            <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            채팅
          </button>
          <button
            onClick={() => { setShowDashboard(true); setShowListPage(false); setActiveId(null); setSelectedProject(null) }}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
              showDashboard
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
            }`}
          >
            <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
              <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
            </svg>
            대시보드
          </button>
        </div>

        {/* 대화 목록 */}
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5 pb-2">
          {filtered.length === 0 ? (
            <p className="text-xs text-zinc-600 text-center mt-8">
              {search ? '검색 결과가 없습니다' : '회의 기록이 없습니다'}
            </p>
          ) : (
            filtered.map(r => (
              <div
                key={r.id}
                onClick={() => {
                  if (menuOpenId === r.id) { setMenuOpenId(null); return }
                  if (renamingId === r.id) return
                  handleSelect(r)
                }}
                className={`group relative flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                  activeId === r.id
                    ? 'bg-zinc-700 text-zinc-100'
                    : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
                }`}
              >
                {renamingId === r.id ? (
                  <input
                    autoFocus
                    value={renameValue}
                    onChange={e => setRenameValue(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleRename(r.id, renameValue)
                      if (e.key === 'Escape') setRenamingId(null)
                    }}
                    onBlur={() => handleRename(r.id, renameValue)}
                    onClick={e => e.stopPropagation()}
                    className="flex-1 text-sm bg-zinc-600 text-zinc-100 rounded px-1.5 py-0.5 outline-none min-w-0"
                  />
                ) : (
                  <div className="flex-1 min-w-0 flex items-center gap-1.5">
                    {r.meetingType === 'system' && (
                      <span className="text-[10px] text-zinc-500 shrink-0">⚙️</span>
                    )}
                    <p className="text-sm truncate">{r.title || '(제목 없음)'}</p>
                  </div>
                )}

                {renamingId !== r.id && (
                  <div className="relative flex-shrink-0">
                    <button
                      onClick={e => {
                        e.stopPropagation()
                        setMenuOpenId(menuOpenId === r.id ? null : r.id)
                      }}
                      onMouseDown={e => e.stopPropagation()}
                      className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-500 hover:text-zinc-200 transition-all"
                    >
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                        <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
                      </svg>
                    </button>

                    {menuOpenId === r.id && (
                      <div
                        onMouseDown={e => e.stopPropagation()}
                        className="absolute right-0 top-6 z-20 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl py-1 w-32"
                      >
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            setRenameValue(r.title || '')
                            setRenamingId(r.id)
                            setMenuOpenId(null)
                          }}
                          className="w-full text-left px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-700 transition-colors"
                        >
                          이름 수정
                        </button>
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            setMenuOpenId(null)
                            handleDelete(r.id)
                          }}
                          className="w-full text-left px-3 py-1.5 text-sm text-red-400 hover:bg-zinc-700 transition-colors"
                        >
                          삭제
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* 다크모드 토글 */}
        <div className="px-2 py-2 border-t border-zinc-800 flex-shrink-0">
          <button
            onClick={() => setDarkMode(d => !d)}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors text-sm"
          >
            {darkMode ? (
              <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4"/>
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>
              </svg>
            ) : (
              <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
              </svg>
            )}
            {darkMode ? '라이트 모드' : '다크 모드'}
          </button>
        </div>
      </aside>

      {/* ── 메인 ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {viewingJobId && !showDashboard && !showListPage && !activeId ? (
          <PipelineView
            jobId={viewingJobId}
            onDone={() => {
              setRunningJobIds(prev => {
                const next = prev.filter(id => id !== viewingJobId)
                if (next.length > 0) {
                  localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(next))
                } else {
                  localStorage.removeItem(ACTIVE_JOB_KEY)
                }
                return next
              })
              setViewingJobId(null)
            }}
          />
        ) : showDashboard && selectedProject ? (
          <DashboardPage
            project={selectedProject}
            onBack={() => setSelectedProject(null)}
            onPipelineStarted={(jobId) => {
              setRunningJobIds(prev => {
                const next = prev.includes(jobId) ? prev : [...prev, jobId]
                localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(next))
                return next
              })
              setViewingJobId(jobId)
              setShowDashboard(false)
              setSelectedProject(null)
            }}
            onDiscordChannelCreated={(channelId) => {
              // 새로 생성된 Discord 채널 ID를 Project에 저장
              const projects = loadProjects()
              const updated = projects.map(p =>
                p.id === selectedProject.id ? { ...p, discordChannelId: channelId } : p
              )
              saveProjects(updated)
              setSelectedProject(prev => prev ? { ...prev, discordChannelId: channelId } : prev)
            }}
          />
        ) : showDashboard ? (
          <ProjectListPage onSelectProject={setSelectedProject} />
        ) : showListPage ? (
          <ChatListPage
            records={records}
            onSelect={handleSelect}
            onNew={startNew}
            onRename={handleRename}
            onDelete={handleDelete}
          />
        ) : (
          <MeetingApp
            key={chatKey}
            initialRecord={activeRecord}
            meetingType={activeMeetingType}
            executionBrief={activeExecutionBrief || undefined}
            onFinished={handleFinished}
            onTitleGenerated={() => setRecords(storage.list())}
            onGoToList={() => {
              setRecords(storage.list())
              setShowListPage(true)
            }}
            onPipelineStarted={(jobId) => {
              setRunningJobIds(prev => {
                const next = prev.includes(jobId) ? prev : [...prev, jobId]
                localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(next))
                return next
              })
              setViewingJobId(jobId)
              setActiveId(null)
            }}
            headerLeft={!sidebarOpen ? mainToggle : undefined}
          />
        )}
      </div>
    </div>
  )
}
