/**
 * CompletionGauge.test.tsx
 *
 * 완성도 게이지 컴포넌트 테스트.
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CompletionGauge } from '../../components/CompletionGauge'

describe('CompletionGauge', () => {
  it('퍼센트 숫자가 렌더링되어야 한다', () => {
    render(<CompletionGauge completeness={78} hint="기술 스택이 비어있습니다" />)
    expect(screen.getByText(/78/)).toBeInTheDocument()
  })

  it('hint 텍스트가 렌더링되어야 한다', () => {
    render(<CompletionGauge completeness={50} hint="목표를 입력해주세요" />)
    expect(screen.getByText('목표를 입력해주세요')).toBeInTheDocument()
  })

  it('0% 상태가 렌더링되어야 한다', () => {
    render(<CompletionGauge completeness={0} hint="" />)
    expect(screen.getByText(/0/)).toBeInTheDocument()
  })

  it('100% 상태가 렌더링되어야 한다', () => {
    render(<CompletionGauge completeness={100} hint="" />)
    expect(screen.getByText(/100/)).toBeInTheDocument()
  })

  it('게이지 바의 width가 completeness 비율을 반영해야 한다', () => {
    const { container } = render(<CompletionGauge completeness={60} hint="" />)
    const bar = container.querySelector('[data-testid="gauge-bar"]')
    expect(bar).toHaveStyle({ width: '60%' })
  })

  it('completeness가 낮으면 빨간색 계열 색상을 사용해야 한다', () => {
    const { container } = render(<CompletionGauge completeness={20} hint="" />)
    const bar = container.querySelector('[data-testid="gauge-bar"]')
    expect(bar?.className).toMatch(/red|orange|yellow/i)
  })

  it('completeness가 높으면 초록색 계열 색상을 사용해야 한다', () => {
    const { container } = render(<CompletionGauge completeness={90} hint="" />)
    const bar = container.querySelector('[data-testid="gauge-bar"]')
    expect(bar?.className).toMatch(/green/i)
  })

  it('hint가 빈 문자열이면 hint 영역이 비어있거나 없어야 한다', () => {
    render(<CompletionGauge completeness={50} hint="" />)
    const hint = screen.queryByTestId('gauge-hint')
    // 없거나 비어있으면 OK
    expect(hint?.textContent ?? '').toBe('')
  })
})
