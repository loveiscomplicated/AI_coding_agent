import { useEffect, useRef, useState, useCallback } from 'react'
import { ChatMessage } from '../types/meeting'
import { parseChoices } from '../utils/choiceParser'

interface Props {
  messages: ChatMessage[]
  isStreaming: boolean
  onChoice?: (text: string) => void
  onRegenerate?: (index: number) => void
}

const BOTTOM_THRESHOLD = 60 // px

export function MessageList({ messages, isStreaming, onChoice, onRegenerate }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [selectedChoices, setSelectedChoices] = useState<Record<number, string[]>>({})

  const isAtBottom = useCallback(() => {
    const el = containerRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
  }, [])

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    setShowScrollBtn(false)
  }, [])

  // 새 메시지가 오면 맨 아래에 있을 때만 자동 스크롤
  useEffect(() => {
    if (isAtBottom()) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isAtBottom])

  const handleScroll = useCallback(() => {
    setShowScrollBtn(!isAtBottom())
  }, [isAtBottom])

  if (messages.length === 0) return null

  return (
    <div className="relative flex-1 overflow-hidden">
    <div
      ref={containerRef}
      data-testid="message-list"
      className="h-full overflow-y-auto px-4 py-3 space-y-4"
      onScroll={handleScroll}
    >
      {messages.map((msg, i) => {
        const isLast = i === messages.length - 1
        const isAssistant = msg.role === 'assistant'

        // assistant 메시지에서 <choice> 태그 파싱: 마지막 메시지가 스트리밍 완료된 경우 선택지 버튼 표시
        // 그 외 assistant 메시지는 태그만 제거하고 내용은 유지
        const { text, choices } =
          isAssistant && isLast && !isStreaming
            ? parseChoices(msg.content)
            : isAssistant
              ? { text: parseChoices(msg.content).text, choices: [] }
              : { text: msg.content, choices: [] }

        const isStreamingThis = isAssistant && isLast && isStreaming

        return (
          <div key={i}>
            <div className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 text-gray-800 dark:text-zinc-100'
                }`}
              >
                {text}
                {isStreamingThis && (
                  <span className="inline-block w-1 h-4 ml-0.5 bg-gray-400 animate-pulse" />
                )}
              </div>
            </div>

            {/* 선택지 버튼 */}
            {choices.length > 0 && onChoice && (() => {
              const selected = selectedChoices[i] ?? []
              return (
                <div className="grid grid-cols-1 gap-2 mt-2 pl-1 max-w-[80%]">
                  {choices.map((choice, j) => {
                    const isSelected = selected.includes(choice)
                    return (
                      <button
                        key={j}
                        className={`w-full rounded-full border px-3.5 py-1.5 text-sm transition-colors text-center ${
                          isSelected
                            ? 'border-blue-500 bg-blue-500 text-white'
                            : 'border-blue-300 dark:border-blue-700 bg-white dark:bg-zinc-800 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-zinc-700 hover:border-blue-400'
                        }`}
                        onClick={() => {
                          setSelectedChoices((prev) => {
                            const cur = prev[i] ?? []
                            const next = cur.includes(choice)
                              ? cur.filter((c) => c !== choice)
                              : [...cur, choice]
                            return { ...prev, [i]: next }
                          })
                        }}
                      >
                        {choice}
                      </button>
                    )
                  })}
                  {selected.length > 0 && (
                    <button
                      className="w-full rounded-full bg-blue-600 hover:bg-blue-700 text-white px-3.5 py-1.5 text-sm font-medium transition-colors"
                      onClick={() => {
                        onChoice(selected.join(', '))
                        setSelectedChoices((prev) => ({ ...prev, [i]: [] }))
                      }}
                    >
                      선택 완료 ({selected.length})
                    </button>
                  )}
                </div>
              )
            })()}

            {/* 새로고침 버튼 — 스트리밍 중이 아닌 모든 assistant 메시지에 표시 */}
            {isAssistant && !isStreamingThis && onRegenerate && (
              <div className="flex justify-start mt-1 pl-1">
                <button
                  className="flex items-center gap-1 text-xs text-gray-400 dark:text-zinc-600 hover:text-gray-600 dark:hover:text-zinc-400 transition-colors"
                  onClick={() => onRegenerate(i)}
                  title="응답 다시 생성"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                    <path d="M3 3v5h5"/>
                  </svg>
                  다시 생성
                </button>
              </div>
            )}
          </div>
        )
      })}
      <div ref={bottomRef} />
    </div>

    {/* 아래로 스크롤 버튼 */}
    {showScrollBtn && (
      <button
        className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full bg-white dark:bg-zinc-800 border border-gray-300 dark:border-zinc-600 shadow-md px-3 py-1.5 text-xs text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-700 transition-all"
        onClick={scrollToBottom}
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="m6 9 6 6 6-6"/>
        </svg>
        맨 아래로
      </button>
    )}
    </div>
  )
}
