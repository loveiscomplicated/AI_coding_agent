/**
 * choiceParser.test.ts
 *
 * <choice> 태그 파싱 및 스트리밍 중 태그 제거 테스트.
 */

import { describe, it, expect } from 'vitest'
import { parseChoices, stripChoiceTags } from '../utils/choiceParser'

const WITH_CHOICES = `
플랫폼을 선택해 주세요.

<choice>📱 iOS</choice>
<choice>🤖 Android</choice>
<choice>📱🤖 둘 다</choice>
`

const WITHOUT_CHOICES = `
기술 스택에 대해 이야기해 봅시다. 어떤 언어를 선호하시나요?
`

const WITH_MULTILINE_CHOICE = `
어떤 스타일인가요?

<choice>A) 도로 바닥에 네온 화살표/라인이 깔리는 느낌</choice>
<choice>B) 공중에 떠다니는 안내 표지판 스타일</choice>
`

describe('parseChoices', () => {
  it('choice 태그가 있으면 choices 배열을 반환해야 한다', () => {
    const { choices } = parseChoices(WITH_CHOICES)
    expect(choices).toHaveLength(3)
    expect(choices[0]).toBe('📱 iOS')
    expect(choices[1]).toBe('🤖 Android')
    expect(choices[2]).toBe('📱🤖 둘 다')
  })

  it('텍스트에서 choice 태그를 제거해야 한다', () => {
    const { text } = parseChoices(WITH_CHOICES)
    expect(text).not.toContain('<choice>')
    expect(text).not.toContain('</choice>')
  })

  it('텍스트 내용은 보존되어야 한다', () => {
    const { text } = parseChoices(WITH_CHOICES)
    expect(text).toContain('플랫폼을 선택해 주세요')
  })

  it('choice 태그가 없으면 빈 배열을 반환해야 한다', () => {
    const { choices } = parseChoices(WITHOUT_CHOICES)
    expect(choices).toEqual([])
  })

  it('choice 태그가 없으면 원본 텍스트를 반환해야 한다', () => {
    const { text } = parseChoices(WITHOUT_CHOICES)
    expect(text.trim()).toBe(WITHOUT_CHOICES.trim())
  })

  it('여러 줄 choice 텍스트를 처리해야 한다', () => {
    const { choices } = parseChoices(WITH_MULTILINE_CHOICE)
    expect(choices).toHaveLength(2)
    expect(choices[0]).toBe('A) 도로 바닥에 네온 화살표/라인이 깔리는 느낌')
  })

  it('빈 문자열은 빈 텍스트와 빈 배열을 반환해야 한다', () => {
    const { text, choices } = parseChoices('')
    expect(text).toBe('')
    expect(choices).toEqual([])
  })

  it('choice 내용의 앞뒤 공백은 제거해야 한다', () => {
    const { choices } = parseChoices('<choice>  iOS  </choice>')
    expect(choices[0]).toBe('iOS')
  })
})

describe('stripChoiceTags', () => {
  it('스트리밍 중 부분적인 태그만 있어도 내용은 보여야 한다', () => {
    const partial = '어떤 플랫폼인가요?\n\n<choice>iOS'
    const result = stripChoiceTags(partial)
    expect(result).toContain('iOS')
    expect(result).not.toContain('<choice>')
  })

  it('닫힌 태그도 제거해야 한다', () => {
    const result = stripChoiceTags('<choice>iOS</choice>')
    expect(result).toBe('iOS')
    expect(result).not.toContain('</choice>')
  })

  it('choice 태그가 없으면 원본을 반환해야 한다', () => {
    expect(stripChoiceTags('일반 텍스트')).toBe('일반 텍스트')
  })
})
