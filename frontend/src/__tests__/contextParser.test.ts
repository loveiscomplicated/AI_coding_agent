/**
 * contextParser.test.ts
 *
 * Opus 응답에서 JSON 블록을 추출하고 파싱하는 함수 테스트.
 *
 * Opus 응답 형식:
 *   텍스트 응답...
 *
 *   ```json
 *   { ...MeetingContext... }
 *   ```
 */

import { describe, it, expect } from 'vitest'
import { extractContext, splitResponse, streamingVisibleText, parseContextDoc } from '../utils/contextParser'

const VALID_JSON_RESPONSE = `
안녕하세요! 프로젝트에 대해 더 알고 싶습니다. 기술 스택을 알려주세요.

\`\`\`json
{
  "version": 1,
  "project": {
    "name": "AI Agent",
    "overview": "코딩 에이전트 시스템",
    "goals": ["빠른 개발"],
    "non_goals": []
  },
  "tech_stack": { "languages": ["Python"], "frameworks": [], "infra": [], "ai_models": [] },
  "constraints": [],
  "milestones": [],
  "agent_config": {
    "orchestrator_model": "claude-opus-4-6",
    "worker_models": [],
    "max_concurrent_agents": 3,
    "sandbox_spec": { "cpu_limit": "", "memory_limit": "", "timeout_minutes": 30 }
  },
  "meeting_meta": { "date": "2026-03-29", "duration_min": 5, "completeness": 20, "version": 1 }
}
\`\`\`
`

const RESPONSE_WITHOUT_JSON = `
기술 스택에 대해 이야기해 봅시다. 어떤 언어를 주로 사용하시나요?
`

const RESPONSE_WITH_INVALID_JSON = `
응답입니다.

\`\`\`json
{ invalid json here :::
\`\`\`
`

const RESPONSE_WITH_MULTIPLE_JSON = `
텍스트입니다.

\`\`\`json
{"first": true}
\`\`\`

또 다른 텍스트.

\`\`\`json
{"second": true}
\`\`\`
`

describe('extractContext', () => {
  it('유효한 JSON 블록에서 컨텍스트를 추출해야 한다', () => {
    const result = extractContext(VALID_JSON_RESPONSE)
    expect(result).not.toBeNull()
    expect(result?.project?.name).toBe('AI Agent')
    expect(result?.project?.goals).toEqual(['빠른 개발'])
  })

  it('JSON 블록이 없으면 null을 반환해야 한다', () => {
    const result = extractContext(RESPONSE_WITHOUT_JSON)
    expect(result).toBeNull()
  })

  it('JSON 파싱 오류 시 null을 반환해야 한다', () => {
    const result = extractContext(RESPONSE_WITH_INVALID_JSON)
    expect(result).toBeNull()
  })

  it('여러 JSON 블록이 있으면 첫 번째를 사용해야 한다', () => {
    const result = extractContext(RESPONSE_WITH_MULTIPLE_JSON)
    expect(result).not.toBeNull()
  })

  it('빈 문자열에서는 null을 반환해야 한다', () => {
    expect(extractContext('')).toBeNull()
  })

  it('tech_stack 필드가 정상 파싱되어야 한다', () => {
    const result = extractContext(VALID_JSON_RESPONSE)
    expect(result?.tech_stack?.languages).toEqual(['Python'])
  })

  it('meeting_meta.completeness가 숫자로 파싱되어야 한다', () => {
    const result = extractContext(VALID_JSON_RESPONSE)
    expect(result?.meeting_meta?.completeness).toBe(20)
  })
})

describe('streamingVisibleText', () => {
  it('```json 이전 텍스트만 반환해야 한다', () => {
    const text = streamingVisibleText('안녕하세요!\n\n```json\n{"version":1}')
    expect(text).toBe('안녕하세요!')
    expect(text).not.toContain('```json')
  })

  it('```json이 없으면 전체 텍스트를 반환해야 한다', () => {
    expect(streamingVisibleText('일반 텍스트입니다.')).toBe('일반 텍스트입니다.')
  })

  it('```json이 등장하는 순간 (불완전한 펜스도) 잘라내야 한다', () => {
    const partial = '텍스트\n\n```json\n{"ver'
    expect(streamingVisibleText(partial)).toBe('텍스트')
  })

  it('빈 문자열은 빈 문자열을 반환해야 한다', () => {
    expect(streamingVisibleText('')).toBe('')
  })

  it('JSON 펜스만 있으면 빈 문자열을 반환해야 한다', () => {
    expect(streamingVisibleText('```json\n{}')).toBe('')
  })

  it('<choice> 태그는 자르지 않고 그대로 유지해야 한다', () => {
    const text = streamingVisibleText('어떤 플랫폼인가요?\n\n<choice>iOS')
    expect(text).toBe('어떤 플랫폼인가요?\n\n<choice>iOS')
  })

  it('<choice>가 있어도 ```json 이전까지만 잘라야 한다', () => {
    const text = streamingVisibleText('텍스트\n<choice>A</choice>\n```json\n{}')
    expect(text).toBe('텍스트\n<choice>A</choice>')
  })

  it('JSON이 <choice>보다 먼저 오면 JSON 기준으로 잘라야 한다', () => {
    const text = streamingVisibleText('텍스트\n```json\n{}\n<choice>A</choice>')
    expect(text).toBe('텍스트')
  })
})

describe('parseContextDoc', () => {
  const VALID_DOC = `---
completeness: 75
hint: 기술 스택 정보가 필요합니다
---

# AI 코딩 에이전트

## 개요
자율적으로 코드를 작성하는 AI 시스템`

  it('frontmatter에서 completeness와 hint를 파싱해야 한다', () => {
    const { meta } = parseContextDoc(VALID_DOC)
    expect(meta.completeness).toBe(75)
    expect(meta.hint).toBe('기술 스택 정보가 필요합니다')
  })

  it('body에 마크다운 본문이 담겨야 한다', () => {
    const { body } = parseContextDoc(VALID_DOC)
    expect(body).toContain('# AI 코딩 에이전트')
    expect(body).not.toContain('completeness:')
  })

  it('completeness는 0~100으로 클램핑되어야 한다', () => {
    const over = parseContextDoc('---\ncompleteness: 150\nhint: test\n---\n본문')
    expect(over.meta.completeness).toBe(100)
    const under = parseContextDoc('---\ncompleteness: -10\nhint: test\n---\n본문')
    expect(under.meta.completeness).toBe(0)
  })

  it('frontmatter가 없으면 completeness=0, hint="" 로 반환해야 한다', () => {
    const { meta, body } = parseContextDoc('# 그냥 마크다운')
    expect(meta.completeness).toBe(0)
    expect(meta.hint).toBe('')
    expect(body).toBe('# 그냥 마크다운')
  })

  it('hint에 따옴표가 있어도 제거되어야 한다', () => {
    const { meta } = parseContextDoc('---\ncompleteness: 50\nhint: "다음 질문"\n---\n본문')
    expect(meta.hint).toBe('다음 질문')
  })
})

describe('splitResponse', () => {
  it('텍스트와 JSON 블록을 분리해야 한다', () => {
    const { text, rawJson } = splitResponse(VALID_JSON_RESPONSE)
    expect(text).toContain('안녕하세요')
    expect(text).not.toContain('```json')
    expect(rawJson).toContain('"AI Agent"')
  })

  it('JSON 블록 없으면 text는 원문 전체, rawJson은 null이어야 한다', () => {
    const { text, rawJson } = splitResponse(RESPONSE_WITHOUT_JSON)
    expect(text).toContain('기술 스택에 대해')
    expect(rawJson).toBeNull()
  })

  it('분리된 text에 ```json 블록이 남아 있으면 안 된다', () => {
    const { text } = splitResponse(VALID_JSON_RESPONSE)
    expect(text).not.toContain('```')
  })

  it('빈 응답은 빈 text와 null rawJson을 반환해야 한다', () => {
    const { text, rawJson } = splitResponse('')
    expect(text.trim()).toBe('')
    expect(rawJson).toBeNull()
  })
})
