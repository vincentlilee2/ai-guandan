// HandCards 组件单测
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// mock Card，避免依赖真实牌面渲染；模拟真实 Card 的 onClick(id) 行为
vi.mock('./Card', () => ({ default: ({ id, selected, onClick }) =>
  <button data-testid="handcard" data-id={id} data-selected={selected} onClick={() => onClick?.(id)}>{`c:${id}`}</button> }))

import HandCards from './HandCards'

// 注册 ref 回调到 Map，供命中检测使用；jsdom 下 rect 为 0 → 拖拽用自定义 rect
function buildRefMap(registerFn, rects = {}) {
  const map = new Map()
  return {
    current: map,
    register: (key, node) => {
      if (node) {
        Object.defineProperty(node, 'getBoundingClientRect', {
          value: () => rects[key] || { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 },
          configurable: true,
        })
        map.set(key, node)
      } else {
        map.delete(key)
      }
      registerFn?.(key, node)
    },
  }
}

const baseProps = {
  cards: ['c1', 'c2', 'c3'],
  selected: ['c2'],
  handDisplayParams: { scale: 0.7, overlap: '-50px', paddingLeft: '32px' },
  isReplayOpen: false,
  onToggleCard: vi.fn(),
  onReorder: vi.fn(),
  cardHitRefs: { current: new Map() },
  registerCardHit: vi.fn(),
}

describe('HandCards', () => {
  it('复盘模式下不渲染', () => {
    const { container } = render(<HandCards {...baseProps} isReplayOpen={true} />)
    expect(container.querySelector('.sm\\:hidden')).toBeNull()
  })

  it('渲染每张牌的 Card（小屏+中大屏双布局共 6 个）', () => {
    render(<HandCards {...baseProps} />)
    expect(screen.getAllByTestId('handcard').length).toBe(6)
  })

  it('选中态透传给 Card', () => {
    render(<HandCards {...baseProps} />)
    const cards = screen.getAllByTestId('handcard')
    // c2 在 selected 里（双布局各一）
    const c2 = cards.filter(el => el.getAttribute('data-id') === 'c2')
    expect(c2.length).toBe(2)
    c2.forEach(el => expect(el.getAttribute('data-selected')).toBe('true'))
    const c1 = cards.filter(el => el.getAttribute('data-id') === 'c1')
    c1.forEach(el => expect(el.getAttribute('data-selected')).toBe('false'))
  })

  it('中大屏点击牌触发 onToggleCard', () => {
    const onToggle = vi.fn()
    const { container } = render(<HandCards {...baseProps} onToggleCard={onToggle} />)
    // 中大屏布局容器
    const largeWrap = container.querySelector('.hidden.sm\\:flex')
    const largeCards = largeWrap.querySelectorAll('[data-testid="handcard"]')
    fireEvent.click(largeCards[0])
    expect(onToggle).toHaveBeenCalledWith('c1')
  })

  it('registerCardHit 在挂载时被调用（小屏 ref 注册）', () => {
    const reg = vi.fn()
    const { rerender } = render(<HandCards {...baseProps} registerCardHit={reg} />)
    // 小屏节点 ref 回调会触发（jsdom 下 ref 会调用）
    expect(reg).toHaveBeenCalled()
    rerender(<HandCards {...baseProps} registerCardHit={reg} cards={['x1']} />)
  })

  it('手牌只剩 1 张时不渲染空第二行（回归：末张牌被下边框遮挡）', () => {
    // 单张牌：第二行为空，不应再出现空行及其 -mt-16 负 margin
    const { container } = render(<HandCards {...baseProps} cards={['c1']} />)
    const small = container.querySelector('.sm\\:hidden')
    // 小屏容器 > 行容器（带 flex items-end 的那层）
    const rows = small.querySelectorAll(':scope > div > div')
    // 非空行容器数量应为 1（空行不再渲染）
    const nonEmptyRows = [...rows].filter(r => r.querySelector('[data-testid="handcard"]'))
    expect(nonEmptyRows.length).toBe(1)
    // 且该行不带负上边距
    expect(nonEmptyRows[0].className).not.toContain('-mt-16')
  })

  it('奇数张手牌第二行为空时不渲染（回归：3 张只有一行）', () => {
    const { container } = render(<HandCards {...baseProps} cards={['c1', 'c2', 'c3']} />)
    const small = container.querySelector('.sm\\:hidden')
    const rows = small.querySelectorAll(':scope > div > div')
    const nonEmptyRows = [...rows].filter(r => r.querySelector('[data-testid="handcard"]'))
    // ceil(3/2)=2 → 第一行 2 张、第二行 1 张；空行不渲染 → 共 2 行
    expect(nonEmptyRows.length).toBe(2)
  })
})

describe('HandCards 拖拽组排', () => {
  // 6 张卡，两行各 3；每张 50x80，行内连续无重叠
  const sixCards = ['a', 'b', 'c', 'd', 'e', 'f']
  const rects = {
    'm:a-0': { left: 0, right: 50, top: 0, bottom: 80, width: 50, height: 80 },
    'm:b-1': { left: 60, right: 110, top: 0, bottom: 80, width: 50, height: 80 },
    'm:c-2': { left: 120, right: 170, top: 0, bottom: 80, width: 50, height: 80 },
    'm:d-3': { left: 0, right: 50, top: 100, bottom: 180, width: 50, height: 80 },
    'm:e-4': { left: 60, right: 110, top: 100, bottom: 180, width: 50, height: 80 },
    'm:f-5': { left: 120, right: 170, top: 100, bottom: 180, width: 50, height: 80 },
    'd:a-0': { left: 0, right: 50, top: 0, bottom: 80, width: 50, height: 80 },
    'd:b-1': { left: 60, right: 110, top: 0, bottom: 80, width: 50, height: 80 },
    'd:c-2': { left: 120, right: 170, top: 0, bottom: 80, width: 50, height: 80 },
    'd:d-3': { left: 180, right: 230, top: 0, bottom: 80, width: 50, height: 80 },
    'd:e-4': { left: 240, right: 290, top: 0, bottom: 80, width: 50, height: 80 },
    'd:f-5': { left: 300, right: 350, top: 0, bottom: 80, width: 50, height: 80 },
  }

  function renderWithDrag(overrides = {}) {
    const refMap = buildRefMap(undefined, rects)
    const props = {
      ...baseProps,
      cards: sixCards,
      onReorder: vi.fn(),
      onToggleCard: vi.fn(),
      cardHitRefs: refMap,
      registerCardHit: refMap.register,
      ...overrides,
    }
    return { ...render(<HandCards {...props} />), props, refMap }
  }

  // 在移动端某张卡上触发完整手势；事件冒泡到持有 handler 的内层容器
  function mobileGesture(key, seq) {
    const node = screen.getByTestId('handcard')
    // 定位到具体卡的 wrapper：data-key 在 Card 外层 div 上，Card 按钮是其后代
    const cardWrap = node.closest('[data-key]')
    const wrap = cardWrap || node
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 0, clientY: 0, bubbles: true })
    seq.forEach(([x, y]) => fireEvent.pointerMove(wrap, { pointerId: 1, clientX: x, clientY: y, bubbles: true }))
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 0, clientY: 0, bubbles: true })
  }

  it('tap（无位移）→ onToggleCard，不触发 onReorder', () => {
    const { props } = renderWithDrag()
    const wrap = screen.getAllByTestId('handcard')[0].closest('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    expect(props.onToggleCard).toHaveBeenCalled()
    expect(props.onReorder).not.toHaveBeenCalled()
  })

  it('手牌只剩 1 张时 tap 仍能选中（回归：末张无法选中、出牌按钮被 disabled）', () => {
    // 单张牌：拖拽不可能，但点选必须仍生效（useHandDrag 不再因 length<2 提前 return）
    const refMap = buildRefMap(undefined, { 'm:solo-0': { left: 0, right: 50, top: 0, bottom: 80, width: 50, height: 80 } })
    const props = {
      ...baseProps,
      cards: ['solo'],
      onReorder: vi.fn(),
      onToggleCard: vi.fn(),
      cardHitRefs: refMap,
      registerCardHit: refMap.register,
    }
    const { container } = render(<HandCards {...props} />)
    const small = container.querySelector('.sm\\:hidden')
    const wrap = small.querySelector('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    expect(props.onToggleCard).toHaveBeenCalledWith('solo')
    expect(props.onReorder).not.toHaveBeenCalled()
  })

  it('指尖抖动：位移 > 阈值但距起点 < 10px → 仍视为点选（修复：抖动 tap 被吞）', () => {
    const { props } = renderWithDrag()
    // a(0) rect [0,50]x[0,80]；按下后抖到 (33, 43)（距起点 ~8.5px，>6px 阈值但 <10px 兜底）
    const wrap = screen.getAllByTestId('handcard')[0].closest('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerMove(wrap, { pointerId: 1, clientX: 33, clientY: 43, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 33, clientY: 43, bubbles: true })
    expect(props.onToggleCard).toHaveBeenCalled()
    expect(props.onReorder).not.toHaveBeenCalled()
  })

  it('位移超过阈值且离开起始牌范围 → 拖拽组排，不触发 onToggleCard', () => {
    const { props } = renderWithDrag()
    // 上行 a(0) 拖到上行末尾（b、c 之后 → slot3）
    const wrap = screen.getAllByTestId('handcard')[0].closest('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerMove(wrap, { pointerId: 1, clientX: 175, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 175, clientY: 40, bubbles: true })
    expect(props.onReorder).toHaveBeenCalledWith(0, 3)
    expect(props.onToggleCard).not.toHaveBeenCalled()
  })

  it('点选相邻牌：无重叠时选中牌不影响点其他牌', () => {
    const { props } = renderWithDrag({ selected: ['b'] })
    // a(0) rect [0,50]x[0,80]，b(1) rect [60,110]x[0,80]，无重叠
    // 点 a(0) → 应选中 a（而非 b）
    const aWrap = screen.getAllByTestId('handcard')[0].closest('[data-key]')
    fireEvent.pointerDown(aWrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerUp(aWrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    expect(props.onToggleCard).toHaveBeenCalledWith('a')
  })

  it('两行：从上行拖到下行 → 扁平索引偏移 half', () => {
    const { props } = renderWithDrag()
    // a(0) 拖到下行开头（y=150 在下行，x=25 → slot0）→ flat = half + 0 = 3
    const wrap = screen.getAllByTestId('handcard')[0].closest('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerMove(wrap, { pointerId: 1, clientX: 25, clientY: 150, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 25, clientY: 150, bubbles: true })
    expect(props.onReorder).toHaveBeenCalledWith(0, 3)
  })

  it('自落（原地放下）→ 不触发 onReorder', () => {
    const { props } = renderWithDrag()
    // b(1) 拖到下个 slot（from+1）→ 无操作
    const wrap = screen.getAllByTestId('handcard')[1].closest('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 85, clientY: 40, bubbles: true })
    fireEvent.pointerMove(wrap, { pointerId: 1, clientX: 100, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 100, clientY: 40, bubbles: true })
    expect(props.onReorder).not.toHaveBeenCalled()
  })

  it('桌面 tap：拖拽 hook 不触发 onToggleCard（suppressTapToggle，避免与 Card onClick 双重触发）', () => {
    const { container, props } = renderWithDrag()
    const desktop = container.querySelector('[data-layout="desktop"]')
    const wrap = desktop.querySelector('[data-key]')
    // 指针序列：按下 + 抬起（无位移）→ 桌面由 Card onClick 处理点选，hook 不应回调
    fireEvent.pointerDown(wrap, { pointerId: 2, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 2, clientX: 25, clientY: 40, bubbles: true })
    expect(props.onToggleCard).not.toHaveBeenCalled()
    // 真正的 click 只触发一次（onClick）
    const btn = desktop.querySelector('button')
    fireEvent.click(btn)
    expect(props.onToggleCard).toHaveBeenCalledTimes(1)
    expect(props.onReorder).not.toHaveBeenCalled()
  })

  it('桌面单行：拖拽组排', () => {
    const { container, props } = renderWithDrag()
    const desktop = container.querySelector('[data-layout="desktop"]')
    const wrap = desktop.querySelector('[data-key]')
    // a(0) 拖到 d(3) 之后 → slot4
    fireEvent.pointerDown(wrap, { pointerId: 2, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerMove(wrap, { pointerId: 2, clientX: 250, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 2, clientX: 250, clientY: 40, bubbles: true })
    expect(props.onReorder).toHaveBeenCalledWith(0, 4)
  })

  it('canDrag=false → 拖拽不触发 onReorder', () => {
    const { props } = renderWithDrag({ canDrag: false })
    const wrap = screen.getAllByTestId('handcard')[0].closest('[data-key]')
    fireEvent.pointerDown(wrap, { pointerId: 1, clientX: 25, clientY: 40, bubbles: true })
    fireEvent.pointerMove(wrap, { pointerId: 1, clientX: 175, clientY: 40, bubbles: true })
    fireEvent.pointerUp(wrap, { pointerId: 1, clientX: 175, clientY: 40, bubbles: true })
    expect(props.onReorder).not.toHaveBeenCalled()
  })
})

