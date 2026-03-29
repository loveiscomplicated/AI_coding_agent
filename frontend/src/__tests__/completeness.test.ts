/**
 * completeness.test.ts
 *
 * 완성도/힌트 함수 테스트.
 * 완성도는 Opus가 meeting_meta.completeness에 직접 설정한 값을 읽는다.
 */

import { describe, it, expect } from 'vitest'
import { calculateCompleteness, getCompletenessHint } from '../utils/completeness'
import { emptyMeetingContext, MeetingContext } from '../types/meeting'

function ctxWithCompleteness(value: number, hint?: string): MeetingContext {
  return {
    version: 1,
    meeting_meta: {
      date: '2026-03-29',
      duration_min: 5,
      completeness: value,
      version: 1,
      ...(hint !== undefined ? { hint } : {}),
    },
  }
}

describe('calculateCompleteness', () => {
  it('빈 컨텍스트는 0%이어야 한다', () => {
    expect(calculateCompleteness(emptyMeetingContext())).toBe(0)
  })

  it('meeting_meta.completeness 값을 그대로 반환한다', () => {
    expect(calculateCompleteness(ctxWithCompleteness(45))).toBe(45)
    expect(calculateCompleteness(ctxWithCompleteness(100))).toBe(100)
    expect(calculateCompleteness(ctxWithCompleteness(0))).toBe(0)
  })

  it('소수점은 반올림된다', () => {
    expect(calculateCompleteness(ctxWithCompleteness(33.7))).toBe(34)
    expect(calculateCompleteness(ctxWithCompleteness(66.4))).toBe(66)
  })

  it('100 초과 값은 100으로 클램핑된다', () => {
    expect(calculateCompleteness(ctxWithCompleteness(150))).toBe(100)
  })

  it('음수 값은 0으로 클램핑된다', () => {
    expect(calculateCompleteness(ctxWithCompleteness(-10))).toBe(0)
  })

  it('meeting_meta가 없어도 크래시 없이 0을 반환한다', () => {
    // @ts-expect-error intentional partial
    const ctx: MeetingContext = { version: 1 }
    expect(() => calculateCompleteness(ctx)).not.toThrow()
    expect(calculateCompleteness(ctx)).toBe(0)
  })
})

describe('getCompletenessHint', () => {
  it('meeting_meta.hint가 있으면 해당 값을 반환한다', () => {
    expect(getCompletenessHint(ctxWithCompleteness(30, '기술 스택을 알려주세요.'))).toBe(
      '기술 스택을 알려주세요.',
    )
  })

  it('hint가 없으면 빈 문자열을 반환한다', () => {
    expect(getCompletenessHint(ctxWithCompleteness(50))).toBe('')
  })

  it('빈 컨텍스트에서 빈 문자열을 반환한다', () => {
    expect(getCompletenessHint(emptyMeetingContext())).toBe('')
  })

  it('meeting_meta가 없어도 크래시 없이 빈 문자열을 반환한다', () => {
    // @ts-expect-error intentional partial
    const ctx: MeetingContext = { version: 1 }
    expect(() => getCompletenessHint(ctx)).not.toThrow()
    expect(getCompletenessHint(ctx)).toBe('')
  })
})
