// RulesModal 单测：渲染/关闭/内容断言
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RulesModal from './RulesModal'

const noop = () => {}

describe('RulesModal', () => {
  it('未打开时不渲染', () => {
    const { container } = render(<RulesModal open={false} onClose={noop} />)
    expect(container.querySelector('.rules-modal')).toBeNull()
  })

  it('打开后渲染标题与免责声明', () => {
    render(<RulesModal open onClose={noop} />)
    expect(screen.getByText('📖 玩法规则')).toBeTruthy()
    expect(screen.getByText(/本项目按掼蛋扑克简化玩法（永远打2）/)).toBeTruthy()
  })

  it('展示计分规则（双游/一三游/单游/炸弹翻倍）', () => {
    render(<RulesModal open onClose={noop} />)
    expect(screen.getByText('📊 计分规则')).toBeTruthy()
    expect(screen.getByText(/双游（队友包揽前两名）/)).toBeTruthy()
    expect(screen.getByText(/一三游（队友分获第一和第三）/)).toBeTruthy()
    expect(screen.getByText(/单游（队友分获第一和第四）/)).toBeTruthy()
    expect(screen.getByText(/炸弹翻倍/)).toBeTruthy()
  })

  it('展示比牌规则关键条目', () => {
    const { container } = render(<RulesModal open onClose={noop} />)
    expect(screen.getByText('🃏 比牌规则')).toBeTruthy()
    // 「炸弹：...」被拆成 key/value 两个 span，用整体文本断言
    expect(container.textContent).toContain('炸弹：4张及以上的炸弹')
    expect(screen.getByText('钢板（两个连续三张）')).toBeTruthy()
    expect(screen.getByText('红桃2（逢人配）')).toBeTruthy()
  })

  it('不显示「其他」小标题、出牌顺序与“/组炸弹”', () => {
    const { container } = render(<RulesModal open onClose={noop} />)
    expect(container.textContent).not.toContain('其他')
    expect(container.textContent).not.toContain('出牌顺序')
    expect(container.textContent).not.toContain('/组炸弹')
  })

  it('点击关闭按钮触发 onClose', () => {
    const onClose = vi.fn()
    render(<RulesModal open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('点击背景遮罩触发 onClose', () => {
    const onClose = vi.fn()
    const { container } = render(<RulesModal open onClose={onClose} />)
    fireEvent.click(container.querySelector('.fixed.inset-0'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
