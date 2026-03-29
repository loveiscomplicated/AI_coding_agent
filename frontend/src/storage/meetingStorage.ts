import { MeetingRecord } from '../types/meeting'

const STORAGE_KEY = 'ai_meeting_records'

export class MeetingStorage {
  private readAll(): MeetingRecord[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      return raw ? (JSON.parse(raw) as MeetingRecord[]) : []
    } catch {
      return []
    }
  }

  private writeAll(records: MeetingRecord[]): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records))
  }

  /** 회의 저장 (id 중복 시 덮어쓰기) */
  save(record: MeetingRecord): void {
    const all = this.readAll()
    const idx = all.findIndex((r) => r.id === record.id)
    if (idx >= 0) {
      all[idx] = record
    } else {
      all.push(record)
    }
    this.writeAll(all)
  }

  /** ID로 단일 회의 조회 */
  get(id: string): MeetingRecord | null {
    return this.readAll().find((r) => r.id === id) ?? null
  }

  /** 전체 목록 (최신 updatedAt 내림차순) */
  list(): MeetingRecord[] {
    return this.readAll().sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    )
  }

  /** 회의 삭제 */
  delete(id: string): void {
    this.writeAll(this.readAll().filter((r) => r.id !== id))
  }
}
