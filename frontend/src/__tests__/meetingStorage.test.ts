/**
 * meetingStorage.test.ts
 *
 * localStorage 기반 회의 저장/불러오기/삭제 테스트.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { MeetingStorage } from '../storage/meetingStorage'
import { ChatMessage, MeetingRecord, emptyMeetingContext } from '../types/meeting'

function makeRecord(overrides: Partial<{ id: string; title: string }> = {}): MeetingRecord {
  return {
    id: overrides.id ?? 'test-id-001',
    title: overrides.title ?? '테스트 회의',
    meetingType: 'project',
    createdAt: '2026-03-29T10:00:00Z',
    updatedAt: '2026-03-29T10:00:00Z',
    messages: [] as ChatMessage[],
    context: emptyMeetingContext(),
    isFinished: false,
  }
}

describe('MeetingStorage', () => {
  let storage: MeetingStorage

  beforeEach(() => {
    localStorage.clear()
    storage = new MeetingStorage()
  })

  // ── 저장 & 불러오기 ─────────────────────────────────────────────────────

  it('회의를 저장하고 ID로 불러올 수 있어야 한다', () => {
    const record = makeRecord()
    storage.save(record)
    const loaded = storage.get(record.id)
    expect(loaded).not.toBeNull()
    expect(loaded?.id).toBe(record.id)
    expect(loaded?.title).toBe('테스트 회의')
  })

  it('존재하지 않는 ID로 불러오면 null을 반환해야 한다', () => {
    expect(storage.get('nonexistent')).toBeNull()
  })

  it('저장된 회의 목록을 반환해야 한다', () => {
    storage.save(makeRecord({ id: 'a', title: '회의 A' }))
    storage.save(makeRecord({ id: 'b', title: '회의 B' }))
    const list = storage.list()
    expect(list).toHaveLength(2)
  })

  it('빈 상태에서 list()는 빈 배열을 반환해야 한다', () => {
    expect(storage.list()).toEqual([])
  })

  // ── 업데이트 ────────────────────────────────────────────────────────────

  it('저장된 회의를 업데이트할 수 있어야 한다', () => {
    const record = makeRecord()
    storage.save(record)
    storage.save({ ...record, title: '수정된 제목' })
    expect(storage.get(record.id)?.title).toBe('수정된 제목')
  })

  it('업데이트 후 list()의 길이가 변하지 않아야 한다', () => {
    storage.save(makeRecord({ id: 'a' }))
    storage.save(makeRecord({ id: 'a', title: '수정' }))
    expect(storage.list()).toHaveLength(1)
  })

  // ── 삭제 ────────────────────────────────────────────────────────────────

  it('회의를 삭제하면 get()이 null을 반환해야 한다', () => {
    const record = makeRecord()
    storage.save(record)
    storage.delete(record.id)
    expect(storage.get(record.id)).toBeNull()
  })

  it('삭제 후 list()에서 제거되어야 한다', () => {
    storage.save(makeRecord({ id: 'a' }))
    storage.save(makeRecord({ id: 'b' }))
    storage.delete('a')
    const list = storage.list()
    expect(list).toHaveLength(1)
    expect(list[0].id).toBe('b')
  })

  it('존재하지 않는 ID 삭제는 오류 없이 무시되어야 한다', () => {
    expect(() => storage.delete('nonexistent')).not.toThrow()
  })

  // ── list() 정렬 ─────────────────────────────────────────────────────────

  it('list()는 최신 업데이트 순(내림차순)으로 반환해야 한다', () => {
    storage.save(makeRecord({ id: 'old', title: '오래된 회의' }))
    storage.save({
      ...makeRecord({ id: 'new', title: '최신 회의' }),
      updatedAt: '2026-03-30T10:00:00Z',
    })
    const list = storage.list()
    expect(list[0].id).toBe('new')
  })

  // ── context 저장 ────────────────────────────────────────────────────────

  it('context가 포함된 회의가 정확히 직렬화/역직렬화되어야 한다', () => {
    const record = makeRecord()
    record.context.project = { name: '테스트 프로젝트', goals: ['목표1', '목표2'] }
    storage.save(record)
    const loaded = storage.get(record.id)
    expect(loaded?.context.project?.name).toBe('테스트 프로젝트')
    expect(loaded?.context.project?.goals).toEqual(['목표1', '목표2'])
  })

  // ── messages 저장 ───────────────────────────────────────────────────────

  it('messages 배열이 정확히 저장/복원되어야 한다', () => {
    const record = makeRecord()
    record.messages = [
      { role: 'user' as const, content: '안녕하세요' },
      { role: 'assistant' as const, content: '반갑습니다' },
    ]
    storage.save(record)
    const loaded = storage.get(record.id)
    expect(loaded?.messages).toHaveLength(2)
    expect(loaded?.messages[0].content).toBe('안녕하세요')
  })
})
