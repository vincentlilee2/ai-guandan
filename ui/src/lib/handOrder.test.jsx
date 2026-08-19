// handOrder.js 纯函数单测
import { describe, it, expect } from 'vitest'
import { reconcileHandOrder, cardsWithKeys, computeRowSplit, rowSlotToFlatIndex } from './handOrder'

describe('reconcileHandOrder', () => {
  it('无用户排列时返回服务器顺序', () => {
    expect(reconcileHandOrder([], ['a', 'b', 'c'])).toEqual(['a', 'b', 'c'])
    expect(reconcileHandOrder(undefined, ['a', 'b'])).toEqual(['a', 'b'])
  })

  it('保留用户相对顺序（集合一致）', () => {
    expect(reconcileHandOrder(['c', 'a', 'b'], ['a', 'b', 'c'])).toEqual(['c', 'a', 'b'])
  })

  it('出牌后剔除已出卡，剩余保持相对顺序', () => {
    // 用户排成 [b,a,c,d]，服务器出掉 a → 剩余 [b,c,d]
    expect(reconcileHandOrder(['b', 'a', 'c', 'd'], ['b', 'c', 'd'])).toEqual(['b', 'c', 'd'])
  })

  it('新出现的卡按服务器顺序追加到尾部', () => {
    // 用户排成 [a,b]，服务器补进 c,d → [a,b,c,d]
    expect(reconcileHandOrder(['a', 'b'], ['a', 'b', 'c', 'd'])).toEqual(['a', 'b', 'c', 'd'])
  })

  it('手牌被整体替换时退化为服务器顺序（重置）', () => {
    // 用户老牌 [a,b,c] 全被换成 [x,y,z] → 服务器顺序
    expect(reconcileHandOrder(['a', 'b', 'c'], ['x', 'y', 'z'])).toEqual(['x', 'y', 'z'])
  })

  it('空服务器手牌返回空', () => {
    expect(reconcileHandOrder(['a', 'b'], [])).toEqual([])
  })

  it('非数组输入安全返回', () => {
    expect(reconcileHandOrder(null, null)).toEqual([])
    expect(reconcileHandOrder(['a'], null)).toEqual(['a'])
  })
})

describe('cardsWithKeys', () => {
  it('生成稳定唯一 key', () => {
    expect(cardsWithKeys(['a', 'b', 'c'])).toEqual([
      { id: 'a', key: 'a-0' },
      { id: 'b', key: 'b-1' },
      { id: 'c', key: 'c-2' },
    ])
  })

  it('重复 id 去重', () => {
    const out = cardsWithKeys(['a', 'a', 'b'])
    expect(out.map(o => o.id)).toEqual(['a', 'b'])
    expect(new Set(out.map(o => o.key)).size).toBe(out.length)
  })
})

describe('computeRowSplit / rowSlotToFlatIndex', () => {
  it('half 向上取整', () => {
    expect(computeRowSplit(6)).toBe(3)
    expect(computeRowSplit(3)).toBe(2)
    expect(computeRowSplit(1)).toBe(1)
  })

  it('row0 映射到前 half，row1 映射到后 half', () => {
    const half = computeRowSplit(5) // 3
    expect(rowSlotToFlatIndex(0, 0, half)).toBe(0)
    expect(rowSlotToFlatIndex(0, 2, half)).toBe(2)
    expect(rowSlotToFlatIndex(1, 0, half)).toBe(3)
    expect(rowSlotToFlatIndex(1, 2, half)).toBe(5)
  })
})
