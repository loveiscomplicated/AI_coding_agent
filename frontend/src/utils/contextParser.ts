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
 * ```json 이 등장하는 순간 그 이전까지만 잘라냅니다.
 */
export function streamingVisibleText(accumulated: string): string {
  const jsonIdx = accumulated.indexOf(JSON_FENCE_START)
  if (jsonIdx === -1) return accumulated
  return accumulated.slice(0, jsonIdx).trimEnd()
}

export interface ContextDocMeta {
  completeness: number
  hint: string
}

/**
 * Haiku가 생성한 컨텍스트 문서에서 YAML frontmatter와 마크다운 본문을 분리합니다.
 *
 * 문서 형식:
 *   ---
 *   completeness: 75
 *   hint: "다음에 필요한 정보"
 *   ---
 *   # 마크다운 본문...
 */
export function parseContextDoc(doc: string): { meta: ContextDocMeta; body: string } {
  const match = doc.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/)
  if (!match) return { meta: { completeness: 0, hint: '' }, body: doc }

  const fm = match[1]
  const body = match[2].trim()
  const completenessStr = (fm.match(/completeness:\s*(\d+)/) ?? [])[1] ?? '0'
  const hintStr = ((fm.match(/hint:\s*["']?([^\n"']+)["']?/) ?? [])[1] ?? '').trim()

  return {
    meta: {
      completeness: Math.min(100, Math.max(0, parseInt(completenessStr, 10))),
      hint: hintStr,
    },
    body,
  }
}
