import { useCallback, useState } from 'react'
import Anthropic from '@anthropic-ai/sdk'
import { ChatMessage } from '../types/meeting'

// ─── 시스템 프롬프트 ──────────────────────────────────────────────────────────

const BASE_SYSTEM_PROMPT = `당신은 프로젝트 기획 파트너입니다.

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

// ─── 컨텍스트 문서 프롬프트 ──────────────────────────────────────────────────

/**
 * Opus용: 최종 상세 문서 (저장 및 실제 활용 목적)
 */
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


// ─── 유틸리티 함수들 ──────────────────────────────────────────────────────────

/**
 * 첫 번째 사용자 메시지를 보고 채팅방 제목을 자동 생성합니다.
 */
export async function generateChatTitle(message: string, apiKey: string): Promise<string> {
  try {
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true })
    const res = await client.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 20,
      messages: [{
        role: 'user',
        content: `다음 내용을 보고 채팅 제목을 한국어로 5단어 이내로 만들어줘. 제목만 출력:\n\n${message.slice(0, 500)}`,
      }],
    })
    const text = res.content[0].type === 'text' ? res.content[0].text.trim() : ''
    return text || '새 회의'
  } catch {
    return '새 회의'
  }
}

function buildContextDocUserContent(messages: ChatMessage[], prevDoc: string): string {
  const conversation = messages
    .map((m) => `${m.role === 'user' ? '사용자' : '파트너'}: ${m.content}`)
    .join('\n\n')
  return prevDoc
    ? `이전 컨텍스트 문서 (참고용):\n${prevDoc}\n\n---\n\n전체 대화:\n${conversation}`
    : `전체 대화:\n${conversation}`
}

/**
 * [Opus] 전체 대화를 바탕으로 상세하고 포괄적인 컨텍스트 문서를 생성합니다.
 * 회의 종료 시 호출됩니다.
 */
export async function generateContextDocWithOpus(
  messages: ChatMessage[],
  prevDoc: string,
  apiKey: string,
): Promise<string> {
  try {
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true })
    const res = await client.messages.create({
      model: 'claude-opus-4-6',
      max_tokens: 16000,
      system: OPUS_CONTEXT_DOC_SYSTEM,
      messages: [{ role: 'user', content: buildContextDocUserContent(messages, prevDoc) }],
    })
    return res.content[0].type === 'text' ? res.content[0].text.trim() : prevDoc
  } catch {
    return prevDoc
  }
}

/**
 * [Opus] 수동 갱신용 스트리밍 버전. onToken으로 실시간 토큰을 전달합니다.
 * signal로 취소 가능합니다.
 */
export async function generateContextDocWithOpusStream(
  messages: ChatMessage[],
  prevDoc: string,
  apiKey: string,
  onToken: (token: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  try {
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true })
    const stream = client.messages.stream({
      model: 'claude-opus-4-6',
      max_tokens: 16000,
      system: OPUS_CONTEXT_DOC_SYSTEM,
      messages: [{ role: 'user', content: buildContextDocUserContent(messages, prevDoc) }],
    })

    if (signal) {
      signal.addEventListener('abort', () => stream.abort())
    }

    let accumulated = ''
    for await (const event of stream) {
      if (signal?.aborted) break
      if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
        accumulated += event.delta.text
        onToken(event.delta.text)
      }
    }
    return accumulated || prevDoc
  } catch {
    return prevDoc
  }
}

// ─── 훅 ───────────────────────────────────────────────────────────────────────

export function useAnthropicStream(apiKey: string) {
  const [isStreaming, setIsStreaming] = useState(false)

  const sendMessage = useCallback(
    async (
      messages: ChatMessage[],
      onToken: (token: string) => void,
      onDone: () => void,
    ): Promise<void> => {
      const client = new Anthropic({
        apiKey,
        dangerouslyAllowBrowser: true,
      })

      setIsStreaming(true)
      try {
        const stream = client.messages.stream({
          model: 'claude-opus-4-6',
          max_tokens: 4096,
          system: BASE_SYSTEM_PROMPT,
          messages: messages.map((m) => {
            if (!m.attachments?.length) return { role: m.role, content: m.content }
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const blocks: any[] = m.attachments.map((att) =>
              att.type === 'image'
                ? { type: 'image', source: { type: 'base64', media_type: att.mediaType, data: att.data } }
                : { type: 'document', source: { type: 'base64', media_type: att.mediaType, data: att.data } }
            )
            if (m.content) blocks.push({ type: 'text', text: m.content })
            return { role: m.role, content: blocks }
          }),
        })

        for await (const event of stream) {
          if (
            event.type === 'content_block_delta' &&
            event.delta.type === 'text_delta'
          ) {
            onToken(event.delta.text)
          }
        }
      } finally {
        setIsStreaming(false)
        onDone()
      }
    },
    [apiKey],
  )

  return { sendMessage, isStreaming }
}
