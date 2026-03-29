import { MeetingContext } from '../types/meeting'

const JSON_BLOCK_RE = /```json\s*([\s\S]*?)```/
const JSON_FENCE_START = '```json'

/**
 * Opus 응답 텍스트에서 첫 번째 ```json 블록을 추출해 파싱합니다.
 * 없거나 파싱 실패 시 null 반환.
 */
export function extractContext(response: string): Partial<MeetingContext> | null {
  const match = response.match(JSON_BLOCK_RE)
  if (!match) return null
  try {
    return JSON.parse(match[1].trim()) as Partial<MeetingContext>
  } catch {
    return null
  }
}

/**
 * Opus 응답을 텍스트 부분과 원본 JSON 문자열로 분리합니다.
 * (스트리밍 완료 후 최종 처리용)
 */
export function splitResponse(response: string): { text: string; rawJson: string | null } {
  const match = response.match(JSON_BLOCK_RE)
  if (!match) return { text: response, rawJson: null }

  const rawJson = match[1].trim()
  const text = response.replace(JSON_BLOCK_RE, '').trim()
  return { text, rawJson }
}

/**
 * 스트리밍 도중 사용자에게 보여줄 텍스트를 반환합니다.
 * ```json 또는 <choice> 태그가 등장하는 순간 그 이전까지만 잘라냅니다.
 * 두 블록 모두 응답 끝에 오므로 이 방식이 안전합니다.
 */
export function streamingVisibleText(accumulated: string): string {
  const jsonIdx = accumulated.indexOf(JSON_FENCE_START)
  const choiceIdx = accumulated.indexOf('<choice>')

  const cuts = [jsonIdx, choiceIdx].filter((i) => i !== -1)
  if (cuts.length === 0) return accumulated

  const cutAt = Math.min(...cuts)
  return accumulated.slice(0, cutAt).trimEnd()
}
