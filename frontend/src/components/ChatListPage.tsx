import { useState, useEffect } from 'react'
import { MeetingRecord } from '../types/meeting'

function relativeTime(dateStr: string): string {
  const now = new Date()
  const date = new Date(dateStr)
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return '방금 전'
  if (diffMins < 60) return `${diffMins}분 전`
  if (diffHours < 24) return `${diffHours}시간 전`
  if (diffDays < 7) return `${diffDays}일 전`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}주 전`
  return date.toLocaleDateString('ko-KR')
}

interface Props {
  records: MeetingRecord[]
  onSelect: (record: MeetingRecord) => void
  onNew: () => void
  onRename: (id: string, newTitle: string) => void
  onDelete: (id: string) => void
}

export function ChatListPage({ records, onSelect, onNew, onRename, onDelete }: Props) {
  const [search, setSearch] = useState('')
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  // 메뉴 외부 클릭 시 닫기
  useEffect(() => {
    if (!menuOpenId) return
    const close = () => setMenuOpenId(null)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpenId])

  const filtered = records.filter(
    r => !search || r.title.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="flex flex-col h-full bg-[#1e1e1e] text-white">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-6 pt-8 pb-5 flex-shrink-0">
        <h1 className="text-2xl font-bold text-white">채팅</h1>
        <button
          onClick={onNew}
          className="flex items-center justify-center w-8 h-8 rounded-lg bg-zinc-700 hover:bg-zinc-600 transition-colors"
          title="새 대화"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>

      {/* 검색 */}
      <div className="px-4 pb-4 flex-shrink-0">
        <div className="flex items-center gap-3 bg-zinc-800 rounded-xl px-4 py-2.5">
          <svg className="w-4 h-4 text-zinc-500 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            placeholder="대화 내용 검색"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="bg-transparent text-sm text-zinc-200 placeholder-zinc-500 outline-none flex-1 min-w-0"
          />
          {search && (
            <button onClick={() => setSearch('')} className="text-zinc-500 hover:text-zinc-300 flex-shrink-0">
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* 목록 */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-zinc-600">
            <svg className="w-10 h-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <p className="text-sm">
              {search ? '검색 결과가 없습니다' : '아직 대화가 없습니다'}
            </p>
            {!search && (
              <button
                onClick={onNew}
                className="text-zinc-400 hover:text-zinc-200 text-sm underline transition-colors"
              >
                첫 번째 대화 시작하기
              </button>
            )}
          </div>
        ) : (
          filtered.map((r, i) => (
            <div key={r.id}>
              <div className="group relative flex items-center px-6 py-4 hover:bg-white/[0.04] transition-colors">
                {/* 제목 / 인라인 이름 수정 */}
                {renamingId === r.id ? (
                  <div className="flex-1 min-w-0 mr-3">
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={e => setRenameValue(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') { onRename(r.id, renameValue); setRenamingId(null) }
                        if (e.key === 'Escape') setRenamingId(null)
                      }}
                      onBlur={() => { onRename(r.id, renameValue); setRenamingId(null) }}
                      onClick={e => e.stopPropagation()}
                      className="w-full text-sm bg-zinc-700 text-zinc-100 rounded px-2 py-0.5 outline-none"
                    />
                  </div>
                ) : (
                  <button
                    className="flex-1 min-w-0 text-left mr-3"
                    onClick={() => onSelect(r)}
                  >
                    <p className="font-semibold text-sm text-white truncate leading-snug">
                      {r.title || '(제목 없음)'}
                      {r.isFinished && (
                        <span className="ml-2 text-xs font-normal text-zinc-500">완료</span>
                      )}
                    </p>
                    <p className="text-xs text-zinc-500 mt-1">
                      마지막 메시지 {relativeTime(r.updatedAt)}
                    </p>
                  </button>
                )}

                {/* 점 세 개 버튼 */}
                {renamingId !== r.id && (
                  <div className="relative flex-shrink-0">
                    <button
                      onClick={e => {
                        e.stopPropagation()
                        setMenuOpenId(menuOpenId === r.id ? null : r.id)
                      }}
                      onMouseDown={e => e.stopPropagation()}
                      className="opacity-0 group-hover:opacity-100 p-1 rounded text-zinc-500 hover:text-zinc-200 transition-all"
                    >
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                        <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
                      </svg>
                    </button>

                    {menuOpenId === r.id && (
                      <div
                        onMouseDown={e => e.stopPropagation()}
                        className="absolute right-0 top-7 z-20 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl py-1 w-32"
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
                            onDelete(r.id)
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

              {i < filtered.length - 1 && (
                <div className="mx-6 border-t border-zinc-800" />
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
