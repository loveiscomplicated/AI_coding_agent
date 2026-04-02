import { useEffect, useRef, useState } from 'react'
import { useMeeting } from '../hooks/useMeeting'
import { MeetingRecord } from '../types/meeting'
import { MessageInput, MessageInputRef, ModelOption } from './MessageInput'
import { MessageList } from './MessageList'
import { TaskDraftPanel } from './TaskDraftPanel'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

interface Props {
  initialRecord?: MeetingRecord
  onFinished?: (record: MeetingRecord) => void
  onGoToList?: () => void
  onTitleGenerated?: () => void
  onPipelineStarted?: (jobId: string) => void
  headerLeft?: React.ReactNode
  meetingType?: 'project' | 'system'
  executionBrief?: string
}

export function MeetingApp({ initialRecord, onFinished, onGoToList, onTitleGenerated, onPipelineStarted, headerLeft, meetingType = 'project', executionBrief }: Props) {
  const resolvedType = initialRecord?.meetingType ?? meetingType

  // 모델 선택 상태
  const [models, setModels] = useState<ModelOption[]>([])
  const [selectedModel, setSelectedModel] = useState<ModelOption | undefined>()

  useEffect(() => {
    fetch(`${API_BASE}/api/chat/models`)
      .then((r) => r.json())
      .then((data) => {
        const list: ModelOption[] = data.models ?? []
        setModels(list)
        // 기본 모델을 목록에서 찾아서 초기 선택
        const defaultId: string | undefined = data.default
        const defaultProvider: string | undefined = data.default_provider
        const found = list.find((m) => m.id === defaultId && m.provider === defaultProvider)
          ?? list.find((m) => m.id === defaultId)
          ?? list[0]
        if (found) setSelectedModel(found)
      })
      .catch(() => {})
  }, [])

  const handleModelChange = (modelId: string) => {
    const found = models.find((m) => m.id === modelId)
    if (found) setSelectedModel(found)
  }

  const meeting = useMeeting(initialRecord, onTitleGenerated, resolvedType, executionBrief, selectedModel?.id, selectedModel?.provider)
  const draftKey = `draft_job_${initialRecord?.id ?? 'new'}`
  const [showDocPanel, setShowDocPanel] = useState(false)
  const [showTaskDraft, setShowTaskDraft] = useState(
    () => !!localStorage.getItem(draftKey)
  )
  const [isDragging, setIsDragging] = useState(false)
  const inputRef = useRef<MessageInputRef>(null)
  const dragCounter = useRef(0)

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
        meetingType: resolvedType,
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

  if (meeting.isFinished && showTaskDraft) {
    return (
      <TaskDraftPanel
        contextDoc={meeting.contextDoc ?? ''}
        draftKey={draftKey}
        onBack={() => { localStorage.removeItem(draftKey); setShowTaskDraft(false) }}
        onPipelineStarted={(jobId) => { localStorage.removeItem(draftKey); onPipelineStarted?.(jobId) }}
      />
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
        <div className="flex gap-3 flex-wrap justify-center">
          <button
            className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm font-medium text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={meeting.resumeMeeting}
          >
            ← 채팅으로 돌아가기
          </button>
          {meeting.contextDoc && (
            <button
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
              onClick={() => { localStorage.setItem(draftKey, '1'); setShowTaskDraft(true) }}
            >
              🚀 태스크 생성
            </button>
          )}
          {onGoToList && (
            <button
              className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm font-medium text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
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

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current++
    if (e.dataTransfer.types.includes('Files')) setIsDragging(true)
  }
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current--
    if (dragCounter.current === 0) setIsDragging(false)
  }
  const handleDragOver = (e: React.DragEvent) => { e.preventDefault() }
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current = 0
    setIsDragging(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) inputRef.current?.addFiles(files)
  }

  return (
    <div
      className="flex flex-col h-full relative"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* 드래그 오버레이 */}
      {isDragging && (
        <div className="absolute inset-0 z-40 bg-blue-50/90 dark:bg-blue-950/80 border-2 border-dashed border-blue-400 dark:border-blue-500 rounded-lg flex flex-col items-center justify-center gap-3 pointer-events-none">
          <svg className="w-10 h-10 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          <p className="text-sm font-medium text-blue-500 dark:text-blue-400">파일을 여기에 놓으세요</p>
          <p className="text-xs text-blue-400 dark:text-blue-500">이미지, PDF 지원</p>
        </div>
      )}

      {/* 헤더 */}
      <div className="flex items-center justify-between px-4 py-3 bg-white dark:bg-zinc-900 border-b border-gray-200 dark:border-zinc-700">
        <div className="flex items-center gap-2">
          {headerLeft}
          <h1 className="text-base font-bold text-gray-800 dark:text-zinc-100">
            {resolvedType === 'system' ? '⚙️ SYSTEM MEETING' : '🏗️ PROJECT MEETING'}
          </h1>
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
      <MessageInput
        ref={inputRef}
        onSend={meeting.sendUserMessage}
        disabled={meeting.isStreaming}
        models={models}
        selectedModel={selectedModel?.id}
        onModelChange={handleModelChange}
      />

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
