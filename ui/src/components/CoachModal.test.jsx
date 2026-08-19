// CoachModal 组件单测：各渲染态（loading / error / reviews / 空 / cached / 关闭回调）
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import CoachModal from './CoachModal'

describe('CoachModal', () => {
  it('open=false 时不渲染', () => {
    const { container } = render(<CoachModal open={false} loading={false} error={null} reviews={[]} cached={false} onClose={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('loading 态显示分析中', () => {
    render(<CoachModal open loading error={null} reviews={null} cached={false} onClose={vi.fn()} />)
    expect(screen.getByText(/教练分析中/)).toBeTruthy()
  })

  it('error 态显示错误文案', () => {
    render(<CoachModal open loading={false} error="AI 教练暂时不可用" reviews={null} cached={false} onClose={vi.fn()} />)
    expect(screen.getByText('AI 教练暂时不可用')).toBeTruthy()
  })

  it('空 reviews 显示提示', () => {
    render(<CoachModal open loading={false} error={null} reviews={[]} cached={false} onClose={vi.fn()} />)
    expect(screen.getByText(/没有 User 出牌记录/)).toBeTruthy()
  })

  it('空 reviews 且带 message 时显示 message', () => {
    render(<CoachModal open loading={false} error={null} reviews={[]} message="本轮您的出牌没有问题！" cached={false} onClose={vi.fn()} />)
    expect(screen.getByText('本轮您的出牌没有问题！')).toBeTruthy()
  })

  it('渲染 reviews 列表（PLAY 与 PASS）', () => {
    const reviews = [
      { action: 'PLAY', desc: '一张4', situation: '开局首发', mistake: '拆了炸弹', advice: '保留炸弹' },
      { action: 'PASS', desc: 'PASS', situation: '队友出大牌', mistake: '无明显错误', advice: '继续让队友' },
    ]
    render(<CoachModal open loading={false} error={null} reviews={reviews} cached={false} onClose={vi.fn()} />)
    expect(screen.getByText('出牌：一张4')).toBeTruthy()
    expect(screen.getByText(/拆了炸弹/)).toBeTruthy()
    expect(screen.getByText(/保留炸弹/)).toBeTruthy()
    // PASS 手
    expect(screen.getByText('PASS')).toBeTruthy()
  })

  it('cached 标记显示', () => {
    render(<CoachModal open loading={false} error={null} reviews={[]} cached onClose={vi.fn()} />)
    expect(screen.getByText(/已缓存结果/)).toBeTruthy()
  })

  it('关闭按钮触发 onClose', () => {
    const onClose = vi.fn()
    render(<CoachModal open loading={false} error={null} reviews={[]} cached={false} onClose={onClose} />)
    fireEvent.click(screen.getByText('关闭'))
    expect(onClose).toHaveBeenCalled()
  })
})
