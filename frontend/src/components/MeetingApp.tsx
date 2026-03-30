import { useEffect, useState } from 'react'
import { useMeeting } from '../hooks/useMeeting'
import { MeetingRecord } from '../types/meeting'
import { MessageInput } from './MessageInput'
import { MessageList } from './MessageList'

interface Props {
  apiKey: string
  initialRecord?: MeetingRecord
  onFinished?: (record: MeetingRecord) => void
  onGoToList?: () => void
  onTitleGenerated?: () => void
  headerLeft?: React.ReactNode
}

export function MeetingApp({ apiKey, initialRecord, onFinished, onGoToList, onTitleGenerated, headerLeft }: Props) {
  const meeting = useMeeting(apiKey, initialRecord, onTitleGenerated)
  const [showDocPanel, setShowDocPanel] = useState(false)

  // ESC 키로 패널 닫기 (갱신 중이면 취소도 함께)
  useEffect(() => {
    if (!showDocPanel) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (meeting.isRefreshing) meeting.abortRefresh()
        setShowDocPanel(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [showDocPanel, meeting.isRefreshing, meeting.abortRefresh])

  const handleRefreshClick = () => {
    setShowDocPanel(true)
    meeting.refreshContextDoc()
  }

  const handleClosePanel = () => {
    if (meeting.isRefreshing) meeting.abortRefresh()
    setShowDocPanel(false)
  }

  const handleFinish = async () => {
    await meeting.finishMeeting()
    if (onFinished) {
      onFinished({
        id: meeting.id,
        title: meeting.context.project?.name || '새 회의',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        messages: meeting.messages,
        context: meeting.context,
        contextDoc: meeting.contextDoc,
        isFinished: true,
      })
    }
  }

  // 회의 종료 버튼 누른 후 Opus가 문서 생성 중인 상태
  if (meeting.isRefreshing && !meeting.isFinished && !showDocPanel) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-500 dark:text-zinc-400">
          대화 내용을 정리하고 있습니다…
        </p>
      </div>
    )
  }

  if (meeting.isFinished) {
    return (
      <div data-testid="meeting-finished" className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <div className="text-4xl">✅</div>
        <h2 className="text-xl font-bold text-gray-800 dark:text-zinc-100">회의 종료 완료</h2>
        <p className="text-sm text-gray-500 dark:text-zinc-400">
          컨텍스트가 저장되었습니다.
        </p>
        <div className="flex gap-3">
          <button
            className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm font-medium text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={meeting.resumeMeeting}
          >
            ← 채팅으로 돌아가기
          </button>
          {onGoToList && (
            <button
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
              onClick={onGoToList}
            >
              목록으로 →
            </button>
          )}
        </div>
        <div className="w-full max-w-lg bg-gray-50 dark:bg-zinc-800 rounded-xl p-4 text-left">
          {meeting.contextDoc ? (
            <pre className="text-xs text-gray-600 dark:text-zinc-400 overflow-auto max-h-64 whitespace-pre-wrap font-sans">
              {meeting.contextDoc}
            </pre>
          ) : (
            <p className="text-xs text-gray-400 dark:text-zinc-600">컨텍스트 문서가 없습니다.</p>
          )}
        </div>
      </div>
    )
  }

  const docContent = meeting.isRefreshing ? meeting.refreshingDoc : meeting.contextDoc

  return (
    <div className="flex flex-col h-full">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700">
        <div className="flex items-center gap-2">
          {headerLeft}
          <h1 className="text-base font-bold text-gray-800 dark:text-zinc-100">🏗️ PROJECT MEETING</h1>
        </div>
        <div className="flex items-center gap-2">
          {meeting.contextDoc && !meeting.isRefreshing && (
            <button
              className="rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
              onClick={() => setShowDocPanel(true)}
              title="생성된 컨텍스트 문서 보기"
            >
              📄 문서
            </button>
          )}
          <button
            className="rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={handleRefreshClick}
            disabled={meeting.isRefreshing || meeting.isStreaming || meeting.messages.length === 0}
            title="컨텍스트 문서를 지금 바로 갱신합니다"
          >
            {meeting.isRefreshing ? '갱신 중…' : '↺ 컨텍스트 갱신'}
          </button>
          <button
            className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
            onClick={handleFinish}
          >
            회의 종료
          </button>
        </div>
      </div>

      {/* 메시지 목록 */}
      <MessageList
        messages={meeting.messages}
        isStreaming={meeting.isStreaming}
        onChoice={meeting.sendUserMessage}
        onRegenerate={meeting.regenerateAt}
      />

      {/* 입력창 */}
      <MessageInput onSend={meeting.sendUserMessage} disabled={meeting.isStreaming} />

      {/* 컨텍스트 문서 패널 */}
      {showDocPanel && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
          onClick={handleClosePanel}
        >
          <div
            className="bg-white dark:bg-zinc-900 rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 패널 헤더 */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-zinc-700">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-gray-800 dark:text-zinc-100">컨텍스트 문서</span>
                {meeting.isRefreshing && (
                  <div className="flex items-center gap-1.5 text-xs text-blue-500">
                    <div className="w-3 h-3 border border-blue-500 border-t-transparent rounded-full animate-spin" />
                    생성 중…
                  </div>
                )}
              </div>
              <button
                className="w-7 h-7 flex items-center justify-center rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-zinc-300 hover:bg-gray-100 dark:hover:bg-zinc-800 transition-colors"
                onClick={handleClosePanel}
                title="닫기 (ESC)"
              >
                ✕
              </button>
            </div>

            {/* 문서 본문 */}
            <div className="flex-1 overflow-auto p-4">
              {docContent ? (
                <pre className="text-xs text-gray-700 dark:text-zinc-300 whitespace-pre-wrap font-sans leading-relaxed">
                  {docContent}
                </pre>
              ) : (
                <p className="text-sm text-gray-400 dark:text-zinc-500 text-center py-8">
                  {meeting.isRefreshing ? '문서를 생성하고 있습니다…' : '아직 생성된 문서가 없습니다.'}
                </p>
              )}
            </div>

            {/* 취소 버튼 (갱신 중일 때만) */}
            {meeting.isRefreshing && (
              <div className="px-4 py-3 border-t border-gray-200 dark:border-zinc-700 flex justify-end">
                <button
                  className="rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
                  onClick={handleClosePanel}
                >
                  취소
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
