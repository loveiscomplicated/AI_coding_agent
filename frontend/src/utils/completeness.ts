import { MeetingContext } from '../types/meeting'

/**
 * 완성도(0~100)를 반환합니다.
 * Opus가 meeting_meta.completeness에 직접 판단한 값을 설정합니다.
 */
export function calculateCompleteness(ctx: MeetingContext): number {
  const value = ctx?.meeting_meta?.completeness ?? 0
  return Math.max(0, Math.min(100, Math.round(value)))
}

/**
 * 게이지 아래 힌트 텍스트를 반환합니다.
 * Opus가 meeting_meta.hint에 다음 필요 정보를 설명합니다.
 */
export function getCompletenessHint(ctx: MeetingContext): string {
  return ctx?.meeting_meta?.hint ?? ''
}
