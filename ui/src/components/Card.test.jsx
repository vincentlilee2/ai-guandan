// Card 组件冒烟测试（展示型组件，验证 rank/suit 解析与选中/点击行为）
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import Card from '../components/Card'

describe('Card 组件', () => {
  it('解析普通牌 ID，渲染正确点数/花色', () => {
    render(<Card id="H2-0" />)
    // 红桃 2：左上角 + 中间大图标都应出现 "2" 和 "♥"（非 small 牌会渲染两次点数）
    expect(screen.getAllByText('2').length).toBeGreaterThan(0)
    expect(screen.getAllByText('♥').length).toBeGreaterThan(0)
  })

  it('解析人头牌映射 (11->J, 15->2)', () => {
    const { rerender } = render(<Card id="S11-0" />)
    expect(screen.getAllByText('J').length).toBeGreaterThan(0) // 黑桃 J
    rerender(<Card id="H15-0" />)
    expect(screen.getAllByText('2').length).toBeGreaterThan(0) // 红桃 2
  })

  it('选中态时应用 card-selected 标记', () => {
    const { container } = render(<Card id="D5-0" selected />)
    // 外层 div 带 card-selected
    const root = container.firstChild
    expect(root.className).toContain('card-selected')
  })

  it('非 small 牌点击触发 onClick（传入 id）', () => {
    const onClick = vi.fn()
    const { container } = render(<Card id="C7-0" onClick={onClick} />)
    const root = container.firstChild // 外层可点击 div
    root.click()
    expect(onClick).toHaveBeenCalledWith('C7-0')
  })

  it('small 牌（scale<0.8）不响应点击', () => {
    const onClick = vi.fn()
    const { container } = render(<Card id="C7-0" scale={0.5} onClick={onClick} />)
    const root = container.firstChild
    root.click()
    expect(onClick).not.toHaveBeenCalled()
  })
})
