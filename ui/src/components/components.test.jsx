// 3.1 拆分后组件单测：PlayedCardBubble / ReplayHandStrip / ResultModal
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// mock Card / SimpleCard，避免依赖真实牌面渲染
vi.mock('./Card', () => ({ default: ({ id, scale }) => <div data-testid="card">{`card:${id}:${scale}`}</div> }))
vi.mock('./SimpleCard', () => ({ default: ({ id, theme }) => <div data-testid="simplecard">{`sc:${id}:${theme}`}</div> }))

import PlayedCardBubble from './PlayedCardBubble'
import ReplayHandStrip from './ReplayHandStrip'
import ResultModal from './ResultModal'

describe('PlayedCardBubble', () => {
  it('无 moves 时渲染占位空 div', () => {
    const { container } = render(<PlayedCardBubble moves={[]} />)
    // 空 moves 返回 <div className="h-12 w-1">
    expect(container.querySelector('.h-12.w-1')).toBeTruthy()
  })

  it('PASS 动作渲染 PASS 文本', () => {
    render(<PlayedCardBubble moves={[{ action: 'PASS', desc: 'PASS' }]} />)
    expect(screen.getByText('PASS')).toBeTruthy()
  })

  it('横向布局 PASS 使用水平胶囊（无 vertical-rl）', () => {
    render(<PlayedCardBubble moves={[{ action: 'PASS', desc: 'PASS' }]} layout="horizontal" />)
    const el = screen.getByText('PASS')
    expect(el.className).toContain('rounded-full')
    expect(el.className).not.toContain('vertical-rl')
  })

  it('纵向布局 PASS 竖排显示（writing-mode:vertical-rl）', () => {
    render(<PlayedCardBubble moves={[{ action: 'PASS', desc: 'PASS' }]} layout="vertical" position="left" />)
    const el = screen.getByText('PASS')
    expect(el.className).toContain('vertical-rl')
    expect(el.className).toContain('rounded-lg')
  })

  it('带 cards 的出牌渲染 Card 组件', () => {
    render(<PlayedCardBubble moves={[{ action: 'PLAY', desc: '出牌', cards: ['c1', 'c2'] }]} />)
    expect(screen.getAllByTestId('card').length).toBe(2)
  })

  it('炸弹描述显示高亮样式（含 炸 字）', () => {
    const { container } = render(<PlayedCardBubble moves={[{ action: 'PLAY', desc: '王炸' }]} />)
    expect(container.querySelector('.bg-yellow-900\\/80')).toBeTruthy()
  })

  it('最多显示最近 3 条', () => {
    const moves = [
      { action: 'PLAY', desc: 'A', cards: ['a'] },
      { action: 'PLAY', desc: 'B', cards: ['b'] },
      { action: 'PLAY', desc: 'C', cards: ['c'] },
      { action: 'PLAY', desc: 'D', cards: ['d'] },
    ]
    render(<PlayedCardBubble moves={moves} />)
    expect(screen.getAllByTestId('card').length).toBe(3)
  })
})

describe('ReplayHandStrip', () => {
  it('空手牌显示「已出完」', () => {
    render(<ReplayHandStrip cards={[]} />)
    expect(screen.getByText('已出完')).toBeTruthy()
  })

  it('渲染每个牌的 SimpleCard', () => {
    render(<ReplayHandStrip cards={['x1', 'x2', 'x3']} variant="top" />)
    expect(screen.getAllByTestId('simplecard').length).toBe(3)
  })

  it('展示玩家标签（对家）', () => {
    render(<ReplayHandStrip cards={['x1']} variant="top" />)
    expect(screen.getByText(/对家/)).toBeTruthy()
  })

  it('vertical 非 compact（无气泡）滚动盒 max-h-[70vh]', () => {
    const { container } = render(<ReplayHandStrip cards={['x1', 'x2', 'x3', 'x4', 'x5']} variant="left" vertical />)
    const scrollBox = container.querySelector('.overflow-y-auto')
    expect(scrollBox).toBeTruthy()
    expect(scrollBox.className).toContain('max-h-[70vh]')
  })

  it('vertical compact（侧家有出牌气泡）滚动盒限高 max-h-[18vh] 且列内牌数更少', () => {
    const { container } = render(<ReplayHandStrip cards={['x1', 'x2', 'x3', 'x4', 'x5', 'x6', 'x7', 'x8', 'x9', 'x10']} variant="left" vertical compact />)
    const scrollBox = container.querySelector('.overflow-y-auto')
    expect(scrollBox.className).toContain('max-h-[18vh]')
    // compact：每列最多 ceil(10/9)=2 张 → 列数 = ceil(10/2) = 5 列
    const cols = scrollBox.querySelectorAll(':scope > div')
    expect(cols.length).toBe(5)
  })
})

describe('ResultModal', () => {
  const baseProps = {
    gameResult: {
      scores: { User: 100, RightBot: -100, PartnerBot: 100, LeftBot: -100 },
      info: { type: '双下', base: 100, mult: 1, capped: false },
      remaining_hands: { LeftBot: ['c1'], RightBot: ['c2', 'c3'] },
    },
    gameResultVisible: true,
    gameId: 'g1',
    lastCompletedGameId: 'g0',
    setGameResultVisible: vi.fn(),
    openReplay: vi.fn(),
    setGameResult: vi.fn(),
    setGameId: vi.fn(),
    setMyHand: vi.fn(),
    resetMoveTracking: vi.fn(),
    closeReplay: vi.fn(),
    setLastCompletedGameId: vi.fn(),
    setStatusMsg: vi.fn(),
    gameOverVoiceTimerRef: { current: null },
    resultShownForGameRef: { current: null },
    setSelected: vi.fn(),
  }

  it('不可见时不渲染', () => {
    const { container } = render(<ResultModal {...baseProps} gameResultVisible={false} />)
    expect(container.querySelector('.game-result-modal')).toBeNull()
  })

  it('可见时渲染标题与结算分数', () => {
    render(<ResultModal {...baseProps} />)
    expect(screen.getByText('🏆 游戏结束')).toBeTruthy()
    expect(screen.getByText('+100')).toBeTruthy() // 我方
    expect(screen.getByText('-100')).toBeTruthy() // 敌方
  })

  it('「再来一局」点击触发重置回调', () => {
    render(<ResultModal {...baseProps} />)
    const btn = screen.getByText('🎮 再来一局')
    btn.click()
    expect(baseProps.setGameResultVisible).toHaveBeenCalledWith(false)
    expect(baseProps.setMyHand).toHaveBeenCalledWith([])
    expect(baseProps.setSelected).toHaveBeenCalledWith([])
  })

  it('「复盘」点击触发 openReplay', () => {
    render(<ResultModal {...baseProps} />)
    const btn = screen.getByText('📹 复盘')
    btn.click()
    expect(baseProps.openReplay).toHaveBeenCalled()
  })

  it('「复盘」点击跳过结算总结语音（cancel TTS + 清 timer）', () => {
    // jsdom 无 speechSynthesis，先补个 stub
    if (!window.speechSynthesis) {
      Object.defineProperty(window, 'speechSynthesis', {
        configurable: true,
        value: { cancel: vi.fn(), speak: vi.fn(), getVoices: vi.fn(() => []) },
      })
    }
    const cancelSpy = vi.spyOn(window.speechSynthesis, 'cancel')
    const timerRef = { current: 12345 }
    render(<ResultModal {...baseProps} gameOverVoiceTimerRef={timerRef} />)
    screen.getByText('📹 复盘').click()
    expect(cancelSpy).toHaveBeenCalled()
    expect(timerRef.current).toBeNull()
    expect(baseProps.openReplay).toHaveBeenCalled()
  })
})
