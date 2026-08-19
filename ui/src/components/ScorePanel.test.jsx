// ScorePanel 单测：会员登录开关关闭 → 纯得分卡（无登录面）；开启 → 正常翻转登录面
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ScorePanel } from './ScorePanel'

const baseProps = {
  isReplayOpen: false,
  handleScoreCardClick: vi.fn(),
  scoreFlipFace: 'score',
  totalScores: { User: 42 },
  isLoggedIn: false,
  userName: '',
  startPage: false,
}

describe('ScorePanel', () => {
  it('会员登录未开通：渲染纯得分卡，无「登录」字样，点击不触发回调', () => {
    render(<ScorePanel {...baseProps} memberLoginEnabled={false} />)
    expect(screen.getByText('我的得分')).toBeTruthy()
    expect(screen.getByText('42')).toBeTruthy()
    expect(screen.queryByText('登录')).toBeNull()
    fireEvent.click(screen.getByRole('button'))
    expect(baseProps.handleScoreCardClick).not.toHaveBeenCalled()
  })

  it('会员登录未开通 + 登录翻转面：仍不显示登录字样（背面不渲染）', () => {
    render(<ScorePanel {...baseProps} scoreFlipFace="login" memberLoginEnabled={false} />)
    expect(screen.queryByText('登录')).toBeNull()
    expect(screen.getByText('我的得分')).toBeTruthy()
  })

  it('会员登录开通：显示「登录」，点击触发回调', () => {
    render(<ScorePanel {...baseProps} memberLoginEnabled={true} />)
    expect(screen.getByText('登录')).toBeTruthy()
    fireEvent.click(screen.getByRole('button'))
    expect(baseProps.handleScoreCardClick).toHaveBeenCalledTimes(1)
  })

  it('会员登录开通 + 已登录：显示用户名', () => {
    render(<ScorePanel {...baseProps} memberLoginEnabled={true} isLoggedIn userName="小明" />)
    expect(screen.getByText('小明')).toBeTruthy()
  })

  it('复盘模式：不渲染按钮', () => {
    render(<ScorePanel {...baseProps} isReplayOpen memberLoginEnabled={true} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
