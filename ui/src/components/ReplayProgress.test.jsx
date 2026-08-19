// 复盘浮动进度条组件单测：范围/值绑定、上一轮/下一轮跳转、进度文案
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import ReplayProgress from './ReplayProgress'

const baseProps = {
  currentIndex: 2,
  total: 10,
  progressText: '第 3/10 手',
  statusText: '复盘播放中',
  onScrub: vi.fn(),
}

describe('ReplayProgress', () => {
  it('渲染 range 并绑定 currentIndex/total', () => {
    render(<ReplayProgress {...baseProps} />)
    const range = screen.getByLabelText('复盘进度')
    expect(range).toBeTruthy()
    expect(Number(range.max)).toBe(9) // total-1
    expect(Number(range.value)).toBe(2)
  })

  it('渲染进度文案与状态', () => {
    render(<ReplayProgress {...baseProps} />)
    expect(screen.getByText('第 3/10 手')).toBeTruthy()
    expect(screen.getByText('复盘播放中')).toBeTruthy()
  })

  it('拖动 range 触发 onScrub(exact)', () => {
    render(<ReplayProgress {...baseProps} />)
    const range = screen.getByLabelText('复盘进度')
    fireEvent.change(range, { target: { value: '7' } })
    expect(baseProps.onScrub).toHaveBeenCalledWith(7, 'exact')
  })

  it('「下一轮」触发 onScrub(skip)', () => {
    render(<ReplayProgress {...baseProps} />)
    fireEvent.click(screen.getByText('下一轮 ▶'))
    expect(baseProps.onScrub).toHaveBeenCalledWith(3, 'skip')
  })

  it('「上一轮」触发 onScrub(prev)', () => {
    render(<ReplayProgress {...baseProps} />)
    fireEvent.click(screen.getByText('◀ 上一轮'))
    expect(baseProps.onScrub).toHaveBeenCalledWith(0, 'prev')
  })
})
