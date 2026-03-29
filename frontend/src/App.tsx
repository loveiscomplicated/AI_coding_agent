import { useState, useEffect } from 'react'
import { MeetingRecord } from './types/meeting'
import { MeetingStorage } from './storage/meetingStorage'
import { MeetingApp } from './components/MeetingApp'

const storage = new MeetingStorage()
const API_KEY = import.meta.env.VITE_ANTHROPIC_API_KEY as string

function SidebarToggleIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 3v18" />
    </svg>
  )
}

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [records, setRecords] = useState<MeetingRecord[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [chatKey, setChatKey] = useState(0)
  const [search, setSearch] = useState('')
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

  const activeRecord = records.find(r => r.id === activeId)
  const filtered = records.filter(r =>
    !search || r.title.toLowerCase().includes(search.toLowerCase())
  )

  const startNew = () => {
    setActiveId(null)
    setChatKey(k => k + 1)
  }

  const handleSelect = (record: MeetingRecord) => {
    setActiveId(record.id)
    setChatKey(k => k + 1)
  }

  const handleDelete = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    storage.delete(id)
    setRecords(storage.list())
    if (activeId === id) {
      setActiveId(null)
      setChatKey(k => k + 1)
    }
  }

  const handleFinished = (record: MeetingRecord) => {
    setRecords(storage.list())
    setActiveId(record.id)
  }

  if (!API_KEY) {
    return (
      <div className="flex items-center justify-center h-screen text-center p-8">
        <div>
          <p className="text-lg font-bold text-red-600 mb-2">API 키가 없습니다</p>
          <p className="text-sm text-gray-500">
            <code>frontend/.env</code> 파일에{' '}
            <code>VITE_ANTHROPIC_API_KEY=sk-ant-...</code>를 설정하세요.
          </p>
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
          <button
            onClick={startNew}
            className="p-1.5 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors"
            title="새 대화"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
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
                onClick={() => handleSelect(r)}
                className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                  activeId === r.id
                    ? 'bg-zinc-700 text-zinc-100'
                    : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
                }`}
              >
                <p className="flex-1 text-sm truncate">{r.title || '(제목 없음)'}</p>
                <button
                  onClick={e => handleDelete(r.id, e)}
                  className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-zinc-600 hover:text-red-400 transition-all flex-shrink-0"
                >
                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                  </svg>
                </button>
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
        <MeetingApp
          key={chatKey}
          apiKey={API_KEY}
          initialRecord={activeRecord}
          onFinished={handleFinished}
          headerLeft={!sidebarOpen ? mainToggle : undefined}
        />
      </div>
    </div>
  )
}
