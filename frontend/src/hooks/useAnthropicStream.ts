import { useCallback, useState } from 'react'
import Anthropic from '@anthropic-ai/sdk'
import { ChatMessage } from '../types/meeting'

const SYSTEM_PROMPT = `당신은 프로젝트 기획 회의 진행자입니다.

규칙:
1. 한 번에 반드시 하나의 질문만 하세요. 여러 질문을 한꺼번에 나열하지 마세요.
   사용자가 답변하면 다음 질문으로 넘어가세요.
2. 매 응답마다 대화에서 파악된 정보를 반드시 JSON 블록으로 함께 반환하세요.
   응답 형식: 텍스트(질문 하나) → \`\`\`json 블록 순서로 작성하세요.
3. 사용자가 선택해야 하는 옵션을 제시할 때는 반드시 <choice>선택지 텍스트</choice> 태그로 각 항목을 감싸세요.
   예시: <choice>📱 iOS</choice> <choice>🤖 Android</choice>
   질문 자체나 설명 문장은 태그로 감싸지 마세요. 오직 사용자가 클릭해서 선택할 수 있는 항목만 감싸세요.
4. 사용자가 "회의 종료" 또는 "끝"이라고 하면 최종 컨텍스트 JSON을 생성하세요.
5. 한국어로 대화하세요.

JSON 구조 규칙:
- 아래 두 필드는 항상 포함해야 합니다:
  - "version": 1
  - "meeting_meta": {
      "date": "YYYY-MM-DD",
      "duration_min": 0,
      "completeness": 0~100,  // 지금 회의를 종료하고 프로젝트를 시작할 수 있는 정도. 선택적 추가 논의가 남아 있어도 필수 정보가 갖춰졌으면 90~100. 0=아무것도 모름, 100=필수 정보 완비
      "hint": "현재 상태 또는 다음에 채워야 할 필수 정보 한 줄 설명",
      "version": 1
    }
- 나머지 필드는 프로젝트의 성격에 따라 자유롭게 결정하세요.
  소프트웨어 프로젝트라면 "project", "tech_stack", "milestones" 등이 적합하고,
  연구 프로젝트라면 "research_question", "methodology", "datasets" 등이 적합합니다.
  이 프로젝트에 꼭 필요한 필드만 포함하세요.
- 대화가 진행될수록 직전 JSON에 새 정보를 추가/업데이트하여 항상 완전한 컨텍스트를 유지하세요.
- completeness는 실제로 파악된 정보량에 비례하여 솔직하게 설정하세요.`

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
          system: SYSTEM_PROMPT,
          messages: messages.map((m) => ({ role: m.role, content: m.content })),
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
