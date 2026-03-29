import { MeetingRecord } from '../types/meeting'

interface Props {
  records: MeetingRecord[]
  onSelect: (record: MeetingRecord) => void
  onNew: () => void
  onDelete: (id: string) => void
}

export function MeetingList({ records, onSelect, onNew, onDelete }: Props) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 bg-white border-b border-gray-200">
        <h1 className="text-base font-bold text-gray-800">🏗️ Project Meetings</h1>
        <button
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 transition-colors"
          onClick={onNew}
        >
          + 새 회의
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {records.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
            <span className="text-3xl">📋</span>
            <p className="text-sm">아직 회의 기록이 없습니다.</p>
            <button
              className="mt-2 text-blue-600 text-sm underline"
              onClick={onNew}
            >
              첫 번째 회의 시작하기
            </button>
          </div>
        ) : (
          records.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between bg-white rounded-xl border border-gray-200 px-4 py-3 hover:border-blue-300 cursor-pointer transition-colors"
              onClick={() => onSelect(r)}
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">
                  {r.title || '(제목 없음)'}
                  {r.isFinished && (
                    <span className="ml-2 text-xs text-green-600 font-normal">완료</span>
                  )}
                </p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {r.context.meeting_meta.completeness}% 완성 ·{' '}
                  {r.updatedAt.slice(0, 10)}
                </p>
              </div>
              <button
                className="ml-3 text-xs text-red-400 hover:text-red-600 px-2 py-1"
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(r.id)
                }}
              >
                삭제
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
