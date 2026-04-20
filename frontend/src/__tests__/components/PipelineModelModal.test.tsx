/**
 * PipelineModelModal.test.tsx
 *
 * 복잡도 기반 자동 선택 토글 동작 검증.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  PipelineModelModal,
  type AvailableModel,
  type PipelineTaskSummary,
} from '../../components/PipelineModelModal'

const MODELS: AvailableModel[] = [
  { id: 'gpt-4o-mini', name: 'GPT-4o mini', provider: 'openai' },
  { id: 'gpt-4o',      name: 'GPT-4o',      provider: 'openai' },
  { id: 'claude-haiku',name: 'Claude Haiku',provider: 'claude' },
  { id: 'claude-opus', name: 'Claude Opus', provider: 'claude' },
]

function renderModal(tasks?: PipelineTaskSummary[]) {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  render(
    <PipelineModelModal
      models={MODELS}
      tasks={tasks}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  )
  return { onConfirm, onCancel }
}

async function enableAutoSplit() {
  const user = userEvent.setup()
  const toggle = screen.getByRole('switch', { name: /최종 실패 시 태스크 자동 분해/i })
  await user.click(toggle)
  return { user, toggle }
}

describe('PipelineModelModal — 복잡도 기반 자동 선택 토글', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // /api/chat/complexity-map fetch 는 테스트에서 차단 — DEFAULT_COMPLEXITY_MAP fallback 사용.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('network disabled in tests')),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('토글은 기본값 OFF로 렌더된다', () => {
    renderModal()
    const toggle = screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    // OFF 상태에서는 매핑 테이블이 표시되지 않는다
    expect(screen.queryByTestId('complexity-mapping-table')).toBeNull()
  })

  it('토글 ON 시 매핑 테이블이 표시된다', async () => {
    const user = userEvent.setup()
    renderModal()

    const toggle = screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i })
    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-checked', 'true')

    const table = screen.getByTestId('complexity-mapping-table')
    expect(table).toBeInTheDocument()

    // 세 tier 및 대표 모델명이 모두 노출된다
    expect(within(table).getByText(/simple/)).toBeInTheDocument()
    expect(within(table).getByText(/standard/)).toBeInTheDocument()
    expect(within(table).getByText(/complex/)).toBeInTheDocument()
    expect(within(table).getByText(/gpt-4.1-mini/)).toBeInTheDocument()
    expect(within(table).getByText(/gemini-2.5-flash-lite/)).toBeInTheDocument()
    expect(within(table).getByText(/gpt-5-mini/)).toBeInTheDocument()
    expect(within(table).getByText(/gemini-3-pro-preview/)).toBeInTheDocument()
  })

  it('토글 ON 시 기존 모델 선택기가 disabled 된다', async () => {
    const user = userEvent.setup()
    renderModal()

    const selectsBefore = screen.getAllByRole('combobox')
    expect(selectsBefore.every((s) => !(s as HTMLSelectElement).disabled)).toBe(true)

    await user.click(screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i }))

    // 코딩 에이전트/오케스트레이터 각각 provider+model 2개씩 → 총 4개 select
    const selectsAfter = screen.getAllByRole('combobox')
    const disabledCount = selectsAfter.filter((s) => (s as HTMLSelectElement).disabled).length
    expect(disabledCount).toBeGreaterThanOrEqual(4)
  })

  it('complexity 라벨 없는 태스크가 있으면 경고가 표시된다', async () => {
    const user = userEvent.setup()
    const tasks: PipelineTaskSummary[] = [
      { id: 't-1', complexity: null },
      { id: 't-2', complexity: 'simple' },
      { id: 't-3' }, // complexity 자체가 없는 케이스도 null 취급
    ]
    renderModal(tasks)

    // OFF 상태에서는 경고 안 보임
    expect(screen.queryByTestId('complexity-missing-warning')).toBeNull()

    await user.click(screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i }))

    const warning = screen.getByTestId('complexity-missing-warning')
    expect(warning).toBeInTheDocument()
    // 2개 태스크 (null + 미지정)가 경고에 반영된다
    expect(warning.textContent).toMatch(/2개/)
  })

  it('모든 태스크에 complexity가 있으면 경고가 표시되지 않는다', async () => {
    const user = userEvent.setup()
    const tasks: PipelineTaskSummary[] = [
      { id: 't-1', complexity: 'simple' },
      { id: 't-2', complexity: 'complex' },
    ]
    renderModal(tasks)

    await user.click(screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i }))
    expect(screen.queryByTestId('complexity-missing-warning')).toBeNull()
  })

  it('onConfirm 호출 시 8번째 인자로 autoSelectByComplexity가 전달된다', async () => {
    const user = userEvent.setup()
    const { onConfirm } = renderModal()

    // 토글 ON
    await user.click(screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i }))
    // 확인 버튼 클릭
    await user.click(screen.getByRole('button', { name: /파이프라인 시작/i }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    const args = onConfirm.mock.calls[0]
    expect(args[7]).toBe(true) // autoSelectByComplexity
    // role_models override가 없으므로 undefined
    expect(args[5]).toBeUndefined()
  })

  it('토글 ON이어도 역할별 오버라이드는 유지되어 onConfirm에 전달된다', async () => {
    const user = userEvent.setup()
    const { onConfirm } = renderModal()

    // 토글 ON
    await user.click(screen.getByRole('switch', { name: /복잡도 기반 자동 선택/i }))

    // 역할별 설정 아코디언 펼치기 (disabled가 아니어야 함)
    const advancedBtn = screen.getByRole('button', { name: /역할별 모델 설정/ })
    expect(advancedBtn).not.toBeDisabled()
    await user.click(advancedBtn)

    // 첫 번째 역할의 provider select 찾기 — "기본값" / provider 옵션이 있는 select
    // 역할별 설정 영역의 select들은 ModelSelect의 select 4개 뒤에 위치
    const allSelects = screen.getAllByRole('combobox')
    // 마지막 6개 중 첫 번째 pair(test_writer) — provider, model
    const testWriterProvider = allSelects[allSelects.length - 6] as HTMLSelectElement
    await user.selectOptions(testWriterProvider, 'claude')

    await user.click(screen.getByRole('button', { name: /파이프라인 시작/i }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    const args = onConfirm.mock.calls[0]
    expect(args[7]).toBe(true) // autoSelectByComplexity
    // role_models override가 유지됨
    expect(args[5]).toBeDefined()
    expect(args[5]!.test_writer?.provider).toBe('claude')
  })

  it('토글 OFF 상태로 confirm 시 autoSelectByComplexity=false가 전달된다', async () => {
    const user = userEvent.setup()
    const { onConfirm } = renderModal()

    await user.click(screen.getByRole('button', { name: /파이프라인 시작/i }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onConfirm.mock.calls[0][7]).toBe(false)
  })
})

describe('PipelineModelModal — intervention auto split 토글', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('network disabled in tests')),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('토글 ON 시 구조 변경 경고 문구가 표시된다', async () => {
    renderModal()
    await enableAutoSplit()

    expect(
      screen.getByText(/실행 중 태스크 구조가 변경됩니다/i),
    ).toBeInTheDocument()
  })
})
