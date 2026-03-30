import { useEffect, useRef, useState, useCallback } from 'react'
import { ChatMessage } from '../types/meeting'
import { parseChoices } from '../utils/choiceParser'

interface Props {
  messages: ChatMessage[]
  isStreaming: boolean
  onChoice?: (text: string) => void
  onRegenerate?: (index: number) => void
}

const BOTTOM_THRESHOLD = 60

function CopyButton({ text, isUser }: { text: string; isUser: boolean }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  const cls = isUser
    ? 'text-blue-200 hover:text-white'
    : 'text-gray-400 dark:text-zinc-500 hover:text-gray-600 dark:hover:text-zinc-300'
  return (
    <button
      className={`flex items-center gap-1 text-sm transition-colors ${cls}`}
      onClick={handleCopy}
      title="복사"
    >
      {copied ? (
        <>
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          복사됨
        </>
      ) : (
        <>
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
            <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
          </svg>
          복사
        </>
      )}
    </button>
  )
}

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
          const isUser = msg.role === 'user'

          const { text, choices } =
            isAssistant && isLast
              ? parseChoices(msg.content)
              : isAssistant
                ? { text: parseChoices(msg.content).text, choices: [] }
                : { text: msg.content, choices: [] }

          const isStreamingThis = isAssistant && isLast && isStreaming
          const showActions = !isStreamingThis

          return (
            <div key={i} className="group">
              <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                {/* 메시지 버블 — 버튼을 위한 하단 여백 포함 */}
                <div
                  className={`relative max-w-[80%] rounded-2xl px-4 pt-2.5 text-sm whitespace-pre-wrap ${
                    showActions ? 'pb-8' : 'pb-2.5'
                  } ${
                    isUser
                      ? 'bg-blue-600 text-white'
                      : 'bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 text-gray-800 dark:text-zinc-100'
                  }`}
                >
                  {/* 첨부파일 */}
                  {msg.attachments && msg.attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-2">
                      {msg.attachments.map((att, j) =>
                        att.type === 'image' ? (
                          <img
                            key={j}
                            src={`data:${att.mediaType};base64,${att.data}`}
                            alt={att.name}
                            className="max-w-xs max-h-64 rounded-lg object-contain"
                          />
                        ) : (
                          <div
                            key={j}
                            className="flex items-center gap-1.5 rounded-lg bg-white/20 dark:bg-black/20 px-2.5 py-1.5"
                          >
                            <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                              <polyline points="14 2 14 8 20 8"/>
                            </svg>
                            <span className="text-xs truncate max-w-[140px]">{att.name}</span>
                          </div>
                        )
                      )}
                    </div>
                  )}

                  {text}
                  {isStreamingThis && (
                    <span className="inline-block w-1 h-4 ml-0.5 bg-gray-400 animate-pulse" />
                  )}

                  {/* 액션 버튼 — 버블 내 오른쪽 아래 */}
                  {showActions && (
                    <div className="absolute bottom-1.5 right-2 flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <CopyButton text={text} isUser={isUser} />
                      {isAssistant && onRegenerate && (
                        <button
                          className="flex items-center gap-1 text-sm text-gray-400 dark:text-zinc-500 hover:text-gray-600 dark:hover:text-zinc-300 transition-colors"
                          onClick={() => onRegenerate(i)}
                          title="응답 다시 생성"
                        >
                          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                            <path d="M3 3v5h5"/>
                          </svg>
                          다시 생성
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* 선택지 버튼 */}
              {choices.length > 0 && onChoice && (() => {
                const selected = selectedChoices[i] ?? []
                const canInteract = !isStreaming
                return (
                  <div className="grid grid-cols-1 gap-2 mt-2 pl-1 max-w-[80%]">
                    {choices.map((choice, j) => {
                      const isSelected = selected.includes(choice)
                      return (
                        <button
                          key={j}
                          disabled={!canInteract}
                          className={`w-full rounded-full border px-3.5 py-1.5 text-sm transition-colors text-center ${
                            !canInteract
                              ? 'border-blue-200 dark:border-blue-900 bg-white dark:bg-zinc-800 text-blue-300 dark:text-blue-700 cursor-default'
                              : isSelected
                                ? 'border-blue-500 bg-blue-500 text-white'
                                : 'border-blue-300 dark:border-blue-700 bg-white dark:bg-zinc-800 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-zinc-700 hover:border-blue-400'
                          }`}
                          onClick={() => {
                            if (!canInteract) return
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
                    {selected.length > 0 && canInteract && (
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
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

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
