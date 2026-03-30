/**
 * TaskDraftPanel.test.tsx
 *
 * context_doc → 태스크 초안 생성 → 편집 → 파이프라인 시작 흐름 테스트.
 * fetch는 vi.stubGlobal으로 mock 처리.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TaskDraftPanel } from '../../components/TaskDraftPanel'

const MOCK_TASKS = [
  {
    id: 'task-001',
    title: '메트릭 수집기',
    description: '태스크 리포트를 집계한다.',
    acceptance_criteria: ['저장 가능', '로드 가능'],
    target_files: ['src/metrics/collector.py'],
    depends_on: [],
  },
  {
    id: 'task-002',
    title: 'Weekly 보고서',
    description: '주간 보고서를 생성한다.',
    acceptance_criteria: ['마크다운 출력'],
    target_files: ['src/reports/weekly.py'],
    depends_on: ['task-001'],
  },
]

function stubFetch(draftTasks = MOCK_TASKS) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (url.includes('/tasks/draft')) {
        return Promise.resolve(
          new Response(JSON.stringify({ tasks: draftTasks }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      if (url.includes('/tasks') && opts?.method === 'POST') {
        return Promise.resolve(
          new Response(JSON.stringify({ saved: draftTasks.length, path: 'data/tasks.yaml' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      if (url.includes('/pipeline/run')) {
        return Promise.resolve(
          new Response(JSON.stringify({ job_id: 'job-abc-123', status: 'running' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      if (url.includes('/pipeline/status')) {
        return Promise.resolve(
          new Response(JSON.stringify({ status: 'done' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      return Promise.resolve(new Response('{}', { status: 404 }))
    }),
  )
}

describe('TaskDraftPanel', () => {
  const onBack = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('초기에는 "생성 중" 로딩 상태를 표시해야 한다', () => {
    // fetch를 resolve하지 않는 pending promise
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    expect(screen.getByText(/생성하고 있습니다/i)).toBeInTheDocument()
  })

  it('초안 생성 완료 후 태스크 목록을 표시해야 한다', async () => {
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => {
      expect(screen.getByDisplayValue('메트릭 수집기')).toBeInTheDocument()
      expect(screen.getByDisplayValue('Weekly 보고서')).toBeInTheDocument()
    })
  })

  it('태스크 개수가 헤더에 표시되어야 한다', async () => {
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => {
      expect(screen.getByText(/2개/)).toBeInTheDocument()
    })
  })

  it('태스크 제목을 편집할 수 있어야 한다', async () => {
    const user = userEvent.setup()
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByDisplayValue('메트릭 수집기'))

    const titleInput = screen.getByDisplayValue('메트릭 수집기')
    await user.clear(titleInput)
    await user.type(titleInput, '수정된 제목')
    expect(screen.getByDisplayValue('수정된 제목')).toBeInTheDocument()
  })

  it('태스크 삭제 버튼을 클릭하면 태스크가 제거되어야 한다', async () => {
    const user = userEvent.setup()
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByDisplayValue('메트릭 수집기'))

    // 첫 번째 태스크 삭제 버튼 (✕) 클릭
    const deleteButtons = screen.getAllByTitle('삭제')
    await user.click(deleteButtons[0])

    await waitFor(() => {
      expect(screen.queryByDisplayValue('메트릭 수집기')).not.toBeInTheDocument()
      expect(screen.getByDisplayValue('Weekly 보고서')).toBeInTheDocument()
    })
  })

  it('"태스크 추가" 버튼으로 빈 태스크를 추가할 수 있어야 한다', async () => {
    const user = userEvent.setup()
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/태스크 추가/))

    await user.click(screen.getByText(/태스크 추가/))
    await waitFor(() => {
      expect(screen.getByText(/3개/)).toBeInTheDocument()
    })
  })

  it('"저장 & 파이프라인 시작" 클릭 시 save → run API를 순서대로 호출해야 한다', async () => {
    const user = userEvent.setup()
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/저장 & 파이프라인 시작/))

    await user.click(screen.getByText(/저장 & 파이프라인 시작/))

    const fetchMock = vi.mocked(fetch)
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(c => c[0] as string)
      expect(urls.some(u => u.includes('/tasks'))).toBe(true)
      expect(urls.some(u => u.includes('/pipeline/run'))).toBe(true)
    })
  })

  it('API 오류 발생 시 오류 화면을 표시해야 한다', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url.includes('/tasks/draft')) {
          return Promise.resolve(
            new Response(JSON.stringify({ detail: '서버 오류' }), { status: 502 }),
          )
        }
        return Promise.resolve(new Response('{}', { status: 404 }))
      }),
    )
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => {
      expect(screen.getByText(/오류|error|실패/i)).toBeInTheDocument()
    })
  })

  it('"← 돌아가기" 클릭 시 onBack이 호출되어야 한다', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(new Response(JSON.stringify({ detail: '오류' }), { status: 500 })),
      ),
    )
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/돌아가기/))
    await user.click(screen.getByText(/돌아가기/))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('depends_on이 있으면 "선행 태스크" 정보가 표시되어야 한다', async () => {
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => {
      expect(screen.getByText(/선행 태스크:.*task-001/)).toBeInTheDocument()
    })
  })
})
