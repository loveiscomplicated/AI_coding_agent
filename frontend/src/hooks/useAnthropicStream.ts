import { useCallback, useState } from 'react'
import { ChatMessage } from '../types/meeting'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

// ─── 시스템 프롬프트 ──────────────────────────────────────────────────────────

const _PROJECT_BASE = `당신은 프로젝트 기획 파트너입니다.

사용자가 가져온 아이디어를 함께 탐색하고 발전시켜 나가세요. 단순히 정보를 수집하는 것이 아니라, 진정한 지적 파트너로서 대화에 참여하세요.

대화 방식:
- 사용자의 답변에서 흥미로운 함의나 잠재적 문제를 발견하면 적극적으로 파고드세요
- 당신 자신의 의견과 분석을 솔직하게 제시하세요 ("제 생각엔...", "이 방향이 더 나을 것 같은데...")
- 여러 각도에서 분석하고, 사용자가 미처 생각하지 못한 부분을 짚어주세요
- 아이디어 간의 모순이나 트레이드오프가 있다면 함께 탐색하세요
- 대화 흐름을 자연스럽게 따라가되, 중요한 미결 사항은 적절한 시점에 되짚으세요
- 한국어로 대화하세요

선택지 사용 (자제):
- 사용자가 명확히 2~3개 옵션 중 하나를 골라야 하는 경우에만 <choice>선택지</choice> 태그를 사용하세요
- 의견 교환, 아이디어 탐색, 열린 질문에는 선택지를 사용하지 마세요

대화 종료:
- 사용자가 직접 종료를 요청하거나 회의 종료 버튼을 누를 때까지 대화를 계속 이어가세요
- 당신이 먼저 "이제 다 됐습니다", "기획이 완성됐습니다" 같은 종결 선언을 하지 마세요`

const _SYSTEM_BASE = `당신은 AI 소프트웨어 개발 시스템의 개선 파트너입니다.

주입된 실행 요약(execution_brief)을 바탕으로 시스템 성능, 비용, 프로세스를 분석하고 개선 방향을 함께 탐색합니다.

대화 방식:
- 실행 요약의 지표(성공률, 재시도율, 소요 시간, 비용 등)를 구체적으로 언급하세요
- "이 패턴은 프롬프트 개선으로 해결될 것 같습니다" 같은 실행 가능한 제안을 하세요
- 프로젝트 내용이 아니라 시스템 운영 방식 개선에 집중하세요
- 사용자의 의견에 당신의 분석을 더해 함께 결론을 도출하세요
- 한국어로 대화하세요

선택지 사용 (자제):
- 사용자가 명확히 2~3개 옵션 중 하나를 골라야 하는 경우에만 <choice>선택지</choice> 태그를 사용하세요

대화 종료:
- 사용자가 직접 종료를 요청할 때까지 대화를 계속 이어가세요`

/**
 * 회의 타입과 execution_brief를 받아 적절한 Opus 시스템 프롬프트를 반환한다.
 */
export function buildSystemPrompt(
  meetingType: 'project' | 'system' = 'project',
  executionBrief?: string,
): string {
  const base = meetingType === 'system' ? _SYSTEM_BASE : _PROJECT_BASE
  if (executionBrief?.trim()) {
    return `${base}\n\n---\n\n${executionBrief.trim()}\n\n---`
  }
  return base
}

// 기본 프롬프트 (하위 호환)
const BASE_SYSTEM_PROMPT = _PROJECT_BASE

// ─── 컨텍스트 문서 프롬프트 ──────────────────────────────────────────────────

const OPUS_CONTEXT_DOC_SYSTEM = `당신은 방금 진행된 프로젝트 기획 대화의 내용을 정리하는 전문 문서 작성자입니다.

아래 대화를 바탕으로 포괄적이고 상세한 프로젝트 컨텍스트 문서를 작성하세요.

문서 작성 요구사항:
- 대화에서 나온 모든 중요한 결정, 아이디어, 논거를 빠짐없이 담으세요
- 단순 나열이 아니라 각 결정의 배경과 이유, 트레이드오프까지 기술하세요
- 탐색했다가 폐기된 방향이 있다면 그것도 이유와 함께 기록하세요
- 모호하거나 아직 미결인 사항은 명시적으로 표시하세요
- 프로젝트 성격에 맞는 구조로 체계적으로 구성하세요
  (소프트웨어: 개요/목표/기술스택/아키텍처/마일스톤/리스크 등)
  (연구: 문제의식/핵심 아이디어/방법론/데이터셋/실험 계획/기여 등)
- 나중에 이 문서만 읽어도 전체 맥락을 파악할 수 있을 만큼 충분히 상세하게 작성하세요

반드시 아래 형식으로 시작하세요:
---
completeness: [0~100 정수. 프로젝트를 바로 시작할 수 있을 만큼 정보가 갖춰진 정도]
hint: [남은 미결 사항 또는 다음 논의 필요 내용]
---`

// ─── 내부 헬퍼 ───────────────────────────────────────────────────────────────

function buildMessages(messages: ChatMessage[]) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return messages.map((m): any => {
    if (!m.attachments?.length) return { role: m.role, content: m.content }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const blocks: any[] = m.attachments.map((att) =>
      att.type === 'image'
        ? { type: 'image', source: { type: 'base64', media_type: att.mediaType, data: att.data } }
        : { type: 'document', source: { type: 'base64', media_type: att.mediaType, data: att.data } }
    )
    if (m.content) blocks.push({ type: 'text', text: m.content })
    return { role: m.role, content: blocks }
  })
}

function buildContextDocUserContent(messages: ChatMessage[], prevDoc: string): string {
  const conversation = messages
    .map((m) => `${m.role === 'user' ? '사용자' : '파트너'}: ${m.content}`)
    .join('\n\n')
  return prevDoc
    ? `이전 컨텍스트 문서 (참고용):\n${prevDoc}\n\n---\n\n전체 대화:\n${conversation}`
    : `전체 대화:\n${conversation}`
}

/** 백엔드 SSE 스트림을 읽어 onToken 콜백을 호출한다. 완료된 전체 텍스트를 반환. */
async function readStream(
  url: string,
  body: object,
  onToken: (token: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let accumulated = ''
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const data = JSON.parse(line.slice(6))
      if (data.type === 'text_delta') {
        accumulated += data.text
        onToken(data.text)
      }
      if (data.type === 'done') return accumulated
      if (data.type === 'error') throw new Error(data.message)
    }
  }
  return accumulated
}

// ─── 유틸리티 함수들 ──────────────────────────────────────────────────────────

export async function generateChatTitle(message: string): Promise<string> {
  try {
    const res = await fetch(`${API_BASE}/api/chat/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        purpose: 'title',
        max_tokens: 20,
        messages: [{
          role: 'user',
          content: `다음 내용을 보고 채팅 제목을 한국어로 5단어 이내로 만들어줘. 제목만 출력:\n\n${message.slice(0, 500)}`,
        }],
      }),
    })
    if (!res.ok) return '새 회의'
    const { text } = await res.json()
    return text?.trim() || '새 회의'
  } catch {
    return '새 회의'
  }
}

export async function generateContextDocWithOpus(
  messages: ChatMessage[],
  prevDoc: string,
  model?: string,
  provider?: string,
): Promise<string> {
  try {
    const res = await fetch(`${API_BASE}/api/chat/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        max_tokens: 16000,
        system: OPUS_CONTEXT_DOC_SYSTEM,
        messages: [{ role: 'user', content: buildContextDocUserContent(messages, prevDoc) }],
        ...(model ? { model } : {}),
        ...(provider ? { provider } : {}),
      }),
    })
    if (!res.ok) return prevDoc
    const { text } = await res.json()
    return text?.trim() || prevDoc
  } catch {
    return prevDoc
  }
}

export async function generateContextDocWithOpusStream(
  messages: ChatMessage[],
  prevDoc: string,
  onToken: (token: string) => void,
  signal?: AbortSignal,
  model?: string,
  provider?: string,
): Promise<string> {
  try {
    const result = await readStream(
      `${API_BASE}/api/chat/stream`,
      {
        max_tokens: 16000,
        system: OPUS_CONTEXT_DOC_SYSTEM,
        messages: [{ role: 'user', content: buildContextDocUserContent(messages, prevDoc) }],
        ...(model ? { model } : {}),
        ...(provider ? { provider } : {}),
      },
      onToken,
      signal,
    )
    return result || prevDoc
  } catch {
    return prevDoc
  }
}

// ─── 훅 ───────────────────────────────────────────────────────────────────────

export function useAnthropicStream(systemPrompt?: string, model?: string, provider?: string) {
  const [isStreaming, setIsStreaming] = useState(false)
  const prompt = systemPrompt ?? BASE_SYSTEM_PROMPT

  const sendMessage = useCallback(
    async (
      messages: ChatMessage[],
      onToken: (token: string) => void,
      onDone: () => void,
    ): Promise<void> => {
      setIsStreaming(true)
      try {
        await readStream(
          `${API_BASE}/api/chat/stream`,
          {
            max_tokens: 4096,
            system: prompt,
            messages: buildMessages(messages),
            ...(model ? { model } : {}),
            ...(provider ? { provider } : {}),
          },
          onToken,
        )
      } finally {
        setIsStreaming(false)
        onDone()
      }
    },
    [prompt, model, provider],
  )

  return { sendMessage, isStreaming }
}
