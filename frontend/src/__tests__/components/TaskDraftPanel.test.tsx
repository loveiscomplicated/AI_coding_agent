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
      // POST /api/tasks/draft → job_id 반환
      if (url.includes('/tasks/draft') && opts?.method === 'POST') {
        return Promise.resolve(
          new Response(JSON.stringify({ job_id: 'draft-job-1' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      // GET /api/tasks/draft/{jobId} → 완료 상태 반환
      if (url.includes('/tasks/draft/')) {
        return Promise.resolve(
          new Response(JSON.stringify({ status: 'done', tasks: draftTasks }), {
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
    localStorage.clear()
    sessionStorage.clear()
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

  it('"저장 & 파이프라인 시작" 클릭 시 confirm 모달에서 "그냥 실행" 선택 후 save → run API를 호출해야 한다', async () => {
    const user = userEvent.setup()
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/저장 & 파이프라인 시작/))

    // 실행 버튼 클릭 → confirm 모달 등장
    await user.click(screen.getByText(/저장 & 파이프라인 시작/))
    await waitFor(() => screen.getByText(/그냥 실행/))
    // "그냥 실행" 클릭 → PipelineModelModal 열림, 여기서는 모달 없이 바로 확인
    await user.click(screen.getByText(/그냥 실행/))

    const fetchMock = vi.mocked(fetch)
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(c => c[0] as string)
      expect(urls.some(u => u.includes('/tasks'))).toBe(true)
    })
  })

  it('API 오류 발생 시 오류 화면을 표시해야 한다', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
        if (url.includes('/tasks/draft') && opts?.method === 'POST') {
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

  // ── Momus Critique 테스트 ────────────────────────────────────────────────────

  const APPROVED_RESULT = {
    verdict: 'APPROVED',
    summary: '구조 양호',
    issues: [],
    suggestions: ['제안1'],
  }

  const NEEDS_REVISION_RESULT = {
    verdict: 'NEEDS_REVISION',
    summary: '수정 필요',
    issues: [
      { task_id: 'task-001', severity: 'ERROR', category: 'sizing', message: '태스크가 너무 큽니다' },
      { task_id: 'GLOBAL', severity: 'WARNING', category: 'dependency', message: '순환 의존성 가능성' },
    ],
    suggestions: [],
  }

  function stubFetchWithCritique(critiqueResult: object, draftTasks = MOCK_TASKS) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
        if (url.includes('/tasks/critique/')) {
          return Promise.resolve(
            new Response(JSON.stringify({ status: 'done', result: critiqueResult }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/tasks/critique') && opts?.method === 'POST') {
          return Promise.resolve(
            new Response(JSON.stringify({ job_id: 'critique-job-1' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/tasks/draft') && opts?.method === 'POST') {
          return Promise.resolve(
            new Response(JSON.stringify({ job_id: 'draft-job-1' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/tasks/draft/')) {
          return Promise.resolve(
            new Response(JSON.stringify({ status: 'done', tasks: draftTasks }), {
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
        return Promise.resolve(new Response('{}', { status: 404 }))
      }),
    )
  }

  it('"🦉 Momus 검토" 클릭 시 POST /api/tasks/critique를 호출해야 한다', async () => {
    const user = userEvent.setup()
    stubFetchWithCritique(APPROVED_RESULT)
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/Momus 검토/))

    await user.click(screen.getByText(/Momus 검토/))

    const fetchMock = vi.mocked(fetch)
    await waitFor(() => {
      const calls = fetchMock.mock.calls
      const critiqueCall = calls.find(
        c => (c[0] as string).includes('/tasks/critique') && (c[1] as RequestInit)?.method === 'POST',
      )
      expect(critiqueCall).toBeDefined()
    })
  })

  it('APPROVED 응답 시 녹색 배너가 표시되어야 한다', async () => {
    const user = userEvent.setup()
    stubFetchWithCritique(APPROVED_RESULT)
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/Momus 검토/))

    await user.click(screen.getByText(/Momus 검토/))

    await waitFor(() => {
      expect(screen.getByText(/✅ Momus: 태스크 구조 승인/)).toBeInTheDocument()
    })
  })

  it('NEEDS_REVISION 시 task_id에 해당하는 태스크 카드 아래 이슈가 표시되어야 한다', async () => {
    const user = userEvent.setup()
    stubFetchWithCritique(NEEDS_REVISION_RESULT)
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/Momus 검토/))

    await user.click(screen.getByText(/Momus 검토/))

    await waitFor(() => {
      expect(screen.getByText('태스크가 너무 큽니다')).toBeInTheDocument()
      expect(screen.getByText(/\[sizing\]/)).toBeInTheDocument()
    })
  })

  it('task_id === "GLOBAL" 이슈는 배너 data-testid="global-issues" 블록에 표시되어야 한다', async () => {
    const user = userEvent.setup()
    stubFetchWithCritique(NEEDS_REVISION_RESULT)
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/Momus 검토/))

    await user.click(screen.getByText(/Momus 검토/))

    await waitFor(() => {
      const globalBlock = document.querySelector('[data-testid="global-issues"]')
      expect(globalBlock).toBeInTheDocument()
      expect(globalBlock?.textContent).toContain('순환 의존성 가능성')
    })
  })

  it('검토 안 한 상태에서 실행 버튼 클릭 시 confirm 모달이 표시되어야 한다', async () => {
    const user = userEvent.setup()
    stubFetch()
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/저장 & 파이프라인 시작/))

    await user.click(screen.getByText(/저장 & 파이프라인 시작/))

    await waitFor(() => {
      expect(screen.getByText(/Momus 검토를 건너뛰시겠어요/)).toBeInTheDocument()
    })
  })

  it('태스크 수정 시 critiqueStatus가 idle로 리셋되어 안내 메시지가 표시되어야 한다', async () => {
    const user = userEvent.setup()
    stubFetchWithCritique(APPROVED_RESULT)
    render(<TaskDraftPanel contextDoc="# 프로젝트" onBack={onBack} />)
    await waitFor(() => screen.getByText(/Momus 검토/))

    await user.click(screen.getByText(/Momus 검토/))
    await waitFor(() => screen.getByText(/✅ Momus: 태스크 구조 승인/))

    // 태스크 제목 편집
    const titleInput = screen.getByDisplayValue('메트릭 수집기')
    await user.clear(titleInput)
    await user.type(titleInput, '수정된 제목')

    await waitFor(() => {
      expect(screen.getByText(/이전 검토 결과가 초기화됐습니다/)).toBeInTheDocument()
    })
  })
})
