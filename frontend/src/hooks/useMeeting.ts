import { useState, useCallback, useRef } from 'react'
import { ChatMessage, MeetingContext, MeetingRecord, emptyMeetingContext } from '../types/meeting'
import { streamingVisibleText, parseContextDoc } from '../utils/contextParser'
import { parseChoices } from '../utils/choiceParser'
import { MeetingStorage } from '../storage/meetingStorage'
import { useAnthropicStream, generateChatTitle, generateContextDocWithOpus, generateContextDocWithOpusStream } from './useAnthropicStream'

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

export interface MeetingState {
  id: string
  messages: ChatMessage[]
  context: MeetingContext
  contextDoc: string
  isFinished: boolean
  isStreaming: boolean
  isRefreshing: boolean
}

export function useMeeting(apiKey: string, initialRecord?: MeetingRecord, onTitleGenerated?: () => void) {
  const storage = useRef(new MeetingStorage()).current
  const meetingId = useRef(initialRecord?.id ?? generateId()).current

  const [messages, setMessages] = useState<ChatMessage[]>(initialRecord?.messages ?? [])
  const [context, setContext] = useState<MeetingContext>(
    initialRecord?.context ?? emptyMeetingContext()
  )
  const [contextDoc, setContextDoc] = useState<string>(initialRecord?.contextDoc ?? '')
  const [isFinished, setIsFinished] = useState(initialRecord?.isFinished ?? false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [refreshingDoc, setRefreshingDoc] = useState('')
  const abortControllerRef = useRef<AbortController | null>(null)

  const { sendMessage, isStreaming } = useAnthropicStream(apiKey)

  const persistState = useCallback(
    (msgs: ChatMessage[], ctx: MeetingContext, finished: boolean, titleOverride?: string, doc?: string) => {
      const now = new Date().toISOString()
      const existing = storage.get(meetingId)
      const record: MeetingRecord = {
        id: meetingId,
        title: titleOverride ?? existing?.title ?? '새 회의',
        createdAt: initialRecord?.createdAt ?? now,
        updatedAt: now,
        messages: msgs,
        context: ctx,
        contextDoc: doc ?? existing?.contextDoc,
        isFinished: finished,
      }
      storage.save(record)
    },
    [meetingId, initialRecord, storage]
  )

  const applyGeneratedTitle = useCallback(
    (newTitle: string) => {
      const existing = storage.get(meetingId)
      if (existing) {
        storage.save({ ...existing, title: newTitle, updatedAt: new Date().toISOString() })
      }
      onTitleGenerated?.()
    },
    [meetingId, storage, onTitleGenerated]
  )

  /**
   * history(user 메시지로 끝나는 배열)를 API에 전송하고 assistant 응답을 스트리밍합니다.
   * 응답 완료 후 백그라운드에서 Haiku로 컨텍스트 문서를 갱신합니다.
   */
  const sendWithHistory = useCallback(
    async (history: ChatMessage[], isFirstMessage = false) => {
      let accumulated = ''
      setMessages([...history, { role: 'assistant', content: '' }])

      await sendMessage(
        history,
        (token) => {
          accumulated += token
          const visibleText = parseChoices(streamingVisibleText(accumulated)).text
          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = { role: 'assistant', content: visibleText }
            return updated
          })
        },
        () => {
          const visibleText = streamingVisibleText(accumulated)

          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = { role: 'assistant', content: visibleText }

            persistState(updated, context, false, undefined, contextDoc)

            if (isFirstMessage) {
              generateChatTitle(history[0].content, apiKey).then(applyGeneratedTitle)
            }

            return updated
          })
        },
      )
    },
    [context, contextDoc, sendMessage, persistState, applyGeneratedTitle, apiKey]
    // contextDoc은 persistState에 전달하기 위해 유지
  )

  const sendUserMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming || isFinished) return
      const userMsg: ChatMessage = { role: 'user', content: text }
      const history = [...messages, userMsg]
      const isFirstMessage = !initialRecord && messages.length === 0
      await sendWithHistory(history, isFirstMessage)
    },
    [messages, isStreaming, isFinished, sendWithHistory, initialRecord]
  )

  /**
   * [Opus] 수동 갱신 또는 회의 종료 시 상세한 컨텍스트 문서를 생성합니다.
   */
  const generateFinalDoc = useCallback(async (msgs: ChatMessage[], finished: boolean) => {
    setIsRefreshing(true)
    try {
      const newDoc = await generateContextDocWithOpus(msgs, contextDoc, apiKey)
      if (!newDoc) return
      const { meta } = parseContextDoc(newDoc)
      const newCtx: MeetingContext = {
        ...context,
        meeting_meta: {
          ...context.meeting_meta,
          completeness: meta.completeness,
          hint: meta.hint,
        },
      }
      setContextDoc(newDoc)
      setContext(newCtx)
      persistState(msgs, newCtx, finished, undefined, newDoc)
      if (finished) setIsFinished(true)
    } finally {
      setIsRefreshing(false)
    }
  }, [contextDoc, context, apiKey, persistState])

  /** 수동으로 컨텍스트 문서를 Opus 스트리밍으로 갱신합니다. */
  const refreshContextDoc = useCallback(async () => {
    if (isRefreshing || isStreaming || messages.length === 0) return
    setIsRefreshing(true)
    setRefreshingDoc('')
    const controller = new AbortController()
    abortControllerRef.current = controller
    try {
      let accumulated = ''
      const newDoc = await generateContextDocWithOpusStream(
        messages,
        contextDoc,
        apiKey,
        (token) => {
          accumulated += token
          setRefreshingDoc(accumulated)
        },
        controller.signal,
      )
      if (!controller.signal.aborted && newDoc !== contextDoc) {
        const { meta } = parseContextDoc(newDoc)
        const newCtx: MeetingContext = {
          ...context,
          meeting_meta: { ...context.meeting_meta, completeness: meta.completeness, hint: meta.hint },
        }
        setContextDoc(newDoc)
        setContext(newCtx)
        persistState(messages, newCtx, false, undefined, newDoc)
      }
    } finally {
      setIsRefreshing(false)
      abortControllerRef.current = null
    }
  }, [isRefreshing, isStreaming, messages, contextDoc, context, apiKey, persistState])

  const abortRefresh = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  const finishMeeting = useCallback(async () => {
    if (isRefreshing || isStreaming) return
    await generateFinalDoc(messages, true)
  }, [isRefreshing, isStreaming, messages, generateFinalDoc])

  const resumeMeeting = useCallback(() => {
    setIsFinished(false)
    persistState(messages, context, false, undefined, contextDoc)
  }, [messages, context, contextDoc, persistState])

  const regenerateAt = useCallback(
    async (assistantIndex: number) => {
      if (isStreaming) return
      if (messages[assistantIndex]?.role !== 'assistant') return

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
    contextDoc,
    refreshingDoc,
    isFinished,
    isStreaming,
    isRefreshing,
    sendUserMessage,
    finishMeeting,
    resumeMeeting,
    regenerateAt,
    refreshContextDoc,
    abortRefresh,
  }
}
