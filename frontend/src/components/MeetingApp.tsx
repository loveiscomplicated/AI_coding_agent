import { useMeeting } from '../hooks/useMeeting'
import { MeetingRecord } from '../types/meeting'
import { CompletionGauge } from './CompletionGauge'
import { MessageInput } from './MessageInput'
import { MessageList } from './MessageList'

interface Props {
  apiKey: string
  initialRecord?: MeetingRecord
  onFinished?: (record: MeetingRecord) => void
  headerLeft?: React.ReactNode
}

export function MeetingApp({ apiKey, initialRecord, onFinished, headerLeft }: Props) {
  const meeting = useMeeting(apiKey, initialRecord)

  const handleFinish = () => {
    meeting.finishMeeting()
    if (onFinished) {
      onFinished({
        id: meeting.id,
        title: meeting.context.project?.name || '새 회의',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messages: meeting.messages,
        context: meeting.context,
        isFinished: true,
      })
    }
  }

  if (meeting.isFinished) {
    return (
      <div data-testid="meeting-finished" className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <div className="text-4xl">✅</div>
        <h2 className="text-xl font-bold text-gray-800 dark:text-zinc-100">회의 종료 완료</h2>
        <p className="text-sm text-gray-500 dark:text-zinc-400">
          컨텍스트가 저장되었습니다. 완성도: {meeting.completeness}%
        </p>
        <div className="flex gap-3">
          <button
            className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm font-medium text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={meeting.resumeMeeting}
          >
            ← 채팅으로 돌아가기
          </button>
          {onFinished && (
            <button
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
              onClick={() =>
                onFinished({
                  id: meeting.id,
                  title: meeting.context.project?.name || '새 회의',
                  createdAt: initialRecord?.createdAt ?? new Date().toISOString(),
                  updatedAt: new Date().toISOString(),
                  messages: meeting.messages,
                  context: meeting.context,
                  isFinished: true,
                })
              }
            >
              목록으로 →
            </button>
          )}
        </div>
        <div className="w-full max-w-lg bg-gray-50 dark:bg-zinc-800 rounded-xl p-4 text-left">
          <p className="text-xs font-mono text-gray-600 dark:text-zinc-400 overflow-auto max-h-64 whitespace-pre">
            {JSON.stringify(meeting.context, null, 2)}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700">
        <div className="flex items-center gap-2">
          {headerLeft}
          <h1 className="text-base font-bold text-gray-800 dark:text-zinc-100">🏗️ PROJECT MEETING</h1>
        </div>
        <button
          className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
          onClick={handleFinish}
        >
          회의 종료
        </button>
      </div>

      {/* 완성도 게이지 */}
      <CompletionGauge completeness={meeting.completeness} hint={meeting.hint} />

      {/* 메시지 목록 */}
      <MessageList
        messages={meeting.messages}
        isStreaming={meeting.isStreaming}
        onChoice={meeting.sendUserMessage}
        onRegenerate={meeting.regenerateAt}
      />

      {/* 입력창 */}
      <MessageInput onSend={meeting.sendUserMessage} disabled={meeting.isStreaming} />
    </div>
  )
}
