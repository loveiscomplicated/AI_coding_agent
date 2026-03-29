import { useState, useCallback, useRef } from 'react'
import { ChatMessage, MeetingContext, MeetingRecord, emptyMeetingContext } from '../types/meeting'
import { extractContext, splitResponse, streamingVisibleText } from '../utils/contextParser'
import { stripChoiceTags } from '../utils/choiceParser'
import { calculateCompleteness, getCompletenessHint } from '../utils/completeness'
import { MeetingStorage } from '../storage/meetingStorage'
import { useAnthropicStream } from './useAnthropicStream'

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

/**
 * 두 객체를 재귀적으로 병합합니다.
 * 배열은 통째로 교체하고, 객체는 재귀 병합하며, 나머지 값은 update 우선입니다.
 * Opus가 이전 대화의 JSON 위에 새 정보를 추가·업데이트할 때 사용합니다.
 */
function deepMerge(
  base: Record<string, unknown>,
  update: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base }
  for (const key of Object.keys(update)) {
    const bv = base[key]
    const uv = update[key]
    if (
      uv !== null &&
      uv !== undefined &&
      typeof uv === 'object' &&
      !Array.isArray(uv) &&
      bv !== null &&
      bv !== undefined &&
      typeof bv === 'object' &&
      !Array.isArray(bv)
    ) {
      result[key] = deepMerge(
        bv as Record<string, unknown>,
        uv as Record<string, unknown>,
      )
    } else if (uv !== undefined) {
      result[key] = uv
    }
  }
  return result
}

export interface MeetingState {
  id: string
  messages: ChatMessage[]
  context: MeetingContext
  completeness: number
  hint: string
  isFinished: boolean
  isStreaming: boolean
}

export function useMeeting(apiKey: string, initialRecord?: MeetingRecord) {
  const storage = useRef(new MeetingStorage()).current
  const meetingId = useRef(initialRecord?.id ?? generateId()).current

  const [messages, setMessages] = useState<ChatMessage[]>(initialRecord?.messages ?? [])
  const [context, setContext] = useState<MeetingContext>(
    initialRecord?.context ?? emptyMeetingContext()
  )
  const [isFinished, setIsFinished] = useState(initialRecord?.isFinished ?? false)

  const completeness = calculateCompleteness(context)
  const hint = getCompletenessHint(context)

  const { sendMessage, isStreaming } = useAnthropicStream(apiKey)

  const persistState = useCallback(
    (msgs: ChatMessage[], ctx: MeetingContext, finished: boolean) => {
      const now = new Date().toISOString()
      const record: MeetingRecord = {
        id: meetingId,
        title: ctx.project?.name || '새 회의',
        createdAt: initialRecord?.createdAt ?? now,
        updatedAt: now,
        messages: msgs,
        context: ctx,
        isFinished: finished,
      }
      storage.save(record)
    },
    [meetingId, initialRecord, storage]
  )

  /**
   * history(user 메시지로 끝나는 배열)를 API에 전송하고 assistant 응답을 스트리밍합니다.
   * messages state를 history + 새 assistant 응답으로 교체합니다.
   */
  const sendWithHistory = useCallback(
    async (history: ChatMessage[]) => {
      let accumulated = ''
      setMessages([...history, { role: 'assistant', content: '' }])

      await sendMessage(
        history,
        (token) => {
          accumulated += token
          const visibleText = stripChoiceTags(streamingVisibleText(accumulated))
          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = { role: 'assistant', content: visibleText }
            return updated
          })
        },
        () => {
          const extracted = extractContext(accumulated)
          const { text: visibleText } = splitResponse(accumulated)

          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = {
              role: 'assistant',
              content: visibleText,
              context: extracted ?? undefined,
            }
            const finalCtx = extracted
              ? (deepMerge(
                  context as Record<string, unknown>,
                  extracted as Record<string, unknown>,
                ) as MeetingContext)
              : context
            setContext(finalCtx)
            persistState(updated, finalCtx, false)
            return updated
          })
        }
      )
    },
    [context, sendMessage, persistState]
  )

  const sendUserMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming || isFinished) return
      const userMsg: ChatMessage = { role: 'user', content: text }
      await sendWithHistory([...messages, userMsg])
    },
    [messages, isStreaming, isFinished, sendWithHistory]
  )

  const finishMeeting = useCallback(() => {
    setIsFinished(true)
    persistState(messages, context, true)
  }, [messages, context, persistState])

  const resumeMeeting = useCallback(() => {
    setIsFinished(false)
    persistState(messages, context, false)
  }, [messages, context, persistState])

  /**
   * 특정 인덱스의 assistant 메시지부터 이후를 모두 제거하고,
   * 직전 user 메시지를 쿼리로 다시 요청합니다.
   */
  const regenerateAt = useCallback(
    async (assistantIndex: number) => {
      if (isStreaming) return
      if (messages[assistantIndex]?.role !== 'assistant') return

      // assistantIndex 이전까지 자르면 마지막이 user 메시지여야 함
      const history = messages.slice(0, assistantIndex)
      if (history.length === 0 || history[history.length - 1].role !== 'user') return

      await sendWithHistory(history)
    },
    [messages, isStreaming, sendWithHistory]
  )

  return {
    id: meetingId,
    messages,
    context,
    completeness,
    hint,
    isFinished,
    isStreaming,
    sendUserMessage,
    finishMeeting,
    resumeMeeting,
    regenerateAt,
  }
}
