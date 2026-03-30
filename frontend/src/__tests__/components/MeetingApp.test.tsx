/**
 * MeetingApp.test.tsx
 *
 * 메인 회의 앱 컴포넌트 통합 테스트.
 * 백엔드 fetch 호출은 mock 처리.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MeetingApp } from '../../components/MeetingApp'

// 스트리밍 SSE 응답을 시뮬레이션하는 헬퍼
function makeStreamResponse(text: string) {
  const encoder = new TextEncoder()
  const chunks = [
    `data: ${JSON.stringify({ type: 'text_delta', text })}\n\n`,
    `data: ${JSON.stringify({ type: 'done' })}\n\n`,
  ]
  let idx = 0
  const stream = new ReadableStream({
    pull(controller) {
      if (idx < chunks.length) {
        controller.enqueue(encoder.encode(chunks[idx++]))
      } else {
        controller.close()
      }
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function makeJsonResponse(data: object) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('MeetingApp', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url.includes('/stream')) {
          return Promise.resolve(makeStreamResponse('안녕하세요! 프로젝트에 대해 이야기해 봐요.'))
        }
        if (url.includes('/complete')) {
          return Promise.resolve(
            makeJsonResponse({
              text: '---\ncompleteness: 80\nhint: 테스트 문서\n---\n# 테스트 프로젝트\n\n내용',
            })
          )
        }
        if (url.includes('/health')) {
          return Promise.resolve(new Response('{"status":"ok"}', { status: 200 }))
        }
        return Promise.resolve(new Response('{}', { status: 404 }))
      }),
    )
  })

  it('초기 상태에서 입력창과 전송 버튼이 있어야 한다', () => {
    render(<MeetingApp />)
    expect(screen.getByPlaceholderText(/메시지/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /전송/i })).toBeInTheDocument()
  })

  it('회의 종료 버튼이 있어야 한다', () => {
    render(<MeetingApp />)
    expect(screen.getByRole('button', { name: /회의 종료/i })).toBeInTheDocument()
  })

  it('빈 메시지는 전송되지 않아야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    await user.click(screen.getByRole('button', { name: /전송/i }))
    expect(screen.queryByTestId('message-list')).not.toBeInTheDocument()
  })

  it('메시지 입력 후 전송하면 메시지 목록에 추가되어야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    const input = screen.getByPlaceholderText(/메시지/i)
    await user.type(input, '안녕하세요')
    await user.click(screen.getByRole('button', { name: /전송/i }))
    await waitFor(() => {
      expect(screen.getByTestId('message-list')).toBeInTheDocument()
    })
    expect(screen.getByText('안녕하세요')).toBeInTheDocument()
  })

  it('전송 후 입력창이 초기화되어야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    const input = screen.getByPlaceholderText(/메시지/i) as HTMLInputElement
    await user.type(input, '테스트 메시지')
    await user.click(screen.getByRole('button', { name: /전송/i }))
    await waitFor(() => expect(input.value).toBe(''))
  })

  it('Enter 키로도 전송되어야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    const input = screen.getByPlaceholderText(/메시지/i)
    await user.type(input, '엔터 테스트{Enter}')
    await waitFor(() => {
      expect(screen.queryByText('엔터 테스트')).toBeInTheDocument()
    })
  })

  it('회의 종료 시 완료 화면이 표시되어야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    await user.click(screen.getByRole('button', { name: /회의 종료/i }))
    await waitFor(() => {
      expect(screen.getByTestId('meeting-finished')).toBeInTheDocument()
    })
  })

  it('완료 화면에 채팅으로 돌아가기 버튼이 있어야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    await user.click(screen.getByRole('button', { name: /회의 종료/i }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /채팅으로 돌아가기/i })).toBeInTheDocument()
    })
  })

  it('채팅으로 돌아가기 클릭 시 채팅 화면으로 복귀해야 한다', async () => {
    const user = userEvent.setup()
    render(<MeetingApp />)
    await user.click(screen.getByRole('button', { name: /회의 종료/i }))
    await waitFor(() => screen.getByTestId('meeting-finished'))
    await user.click(screen.getByRole('button', { name: /채팅으로 돌아가기/i }))
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/메시지/i)).toBeInTheDocument()
      expect(screen.queryByTestId('meeting-finished')).not.toBeInTheDocument()
    })
  })
})
