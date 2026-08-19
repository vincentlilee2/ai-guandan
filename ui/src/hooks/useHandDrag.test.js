// useHandDrag 纯几何函数单测（无 DOM，直接构造 rect 数据）
import { describe, it, expect } from 'vitest'
import { measureGeometry, computeDropIndex } from '../hooks/useHandDrag'
import { cardsWithKeys } from '../lib/handOrder'

// 构造一个 cardHitRefs Map：lookupKey -> { getBoundingClientRect }
function makeRefMap(cards, rectsByKey) {
  const items = cardsWithKeys(cards)
  const map = new Map()
  items.forEach((item) => {
    const rect = rectsByKey[item.key] || { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }
    map.set(item.key, { getBoundingClientRect: () => rect })
  })
  return { current: map }
}

describe('measureGeometry', () => {
  it('两行：按 half 拆分上下行', () => {
    // 6 张卡 → 3/3；每张 50x80，行内无重叠（连续排布）
    const rects = {
      'a-0': { left: 0, right: 50, top: 0, bottom: 80, width: 50, height: 80 },
      'b-1': { left: 60, right: 110, top: 0, bottom: 80, width: 50, height: 80 },
      'c-2': { left: 120, right: 170, top: 0, bottom: 80, width: 50, height: 80 },
      'd-3': { left: 0, right: 50, top: 100, bottom: 180, width: 50, height: 80 },
      'e-4': { left: 60, right: 110, top: 100, bottom: 180, width: 50, height: 80 },
      'f-5': { left: 120, right: 170, top: 100, bottom: 180, width: 50, height: 80 },
    }
    const refs = makeRefMap(['a', 'b', 'c', 'd', 'e', 'f'], rects)
    const g = measureGeometry(['a', 'b', 'c', 'd', 'e', 'f'], refs, 'two')
    expect(g.rows.length).toBe(2)
    expect(g.half).toBe(3)
    expect(g.rows[0].centers).toEqual([25, 85, 145])
    expect(g.rows[1].centers).toEqual([25, 85, 145])
    expect(g.rows[0].cy).toBe(40)
    expect(g.rows[1].cy).toBe(140)
  })

  it('奇数张（3）→ 上行 2 / 下行 1', () => {
    const rects = {
      'a-0': { left: 0, right: 50, top: 0, bottom: 80, width: 50, height: 80 },
      'b-1': { left: 60, right: 110, top: 0, bottom: 80, width: 50, height: 80 },
      'c-2': { left: 30, right: 80, top: 100, bottom: 180, width: 50, height: 80 },
    }
    const refs = makeRefMap(['a', 'b', 'c'], rects)
    const g = measureGeometry(['a', 'b', 'c'], refs, 'two')
    expect(g.half).toBe(2)
    expect(g.rows[0].centers).toEqual([25, 85])
    expect(g.rows[1].centers).toEqual([55])
  })

  it('jsdom 零尺寸 rect 被跳过（无几何可用）', () => {
    const refs = makeRefMap(['a', 'b'], {})
    const g = measureGeometry(['a', 'b'], refs, 'two')
    expect(g.rows.length).toBe(0)
  })
})

describe('computeDropIndex', () => {
  const twoRowGeom = {
    half: 3,
    rows: [
      { centers: [25, 85, 145], left: 0, right: 170, cy: 40 },
      { centers: [25, 85, 145], left: 0, right: 170, cy: 140 },
    ],
  }

  it('两行：上行 slot0（最左）→ 扁平 0', () => {
    expect(computeDropIndex(5, 30, twoRowGeom, 'two')).toBe(0)
  })

  it('两行：上行两张之间 → 扁平 1/2', () => {
    // 中心 25 与 85，gap=(170-0)/2=85；最近中心判断
    expect(computeDropIndex(50, 30, twoRowGeom, 'two')).toBe(1)
    expect(computeDropIndex(110, 30, twoRowGeom, 'two')).toBe(2)
  })

  it('两行：下行 → 扁平索引偏移 half', () => {
    // 下行 slot0 → 3；下行 slot2（两张之间）→ 3+2=5
    expect(computeDropIndex(5, 150, twoRowGeom, 'two')).toBe(3)
    expect(computeDropIndex(50, 150, twoRowGeom, 'two')).toBe(4)
    expect(computeDropIndex(150, 150, twoRowGeom, 'two')).toBe(6)
  })

  it('两行：行边界（下行中线以下）→ 下行', () => {
    // y=90 低于 (40+140)/2=90 → 下行
    expect(computeDropIndex(5, 91, twoRowGeom, 'two')).toBe(3)
  })

  it('单行：始终 row0，扁平索引即 slot', () => {
    const oneRowGeom = { half: 3, rows: [{ centers: [25, 85, 145], left: 0, right: 170, cy: 40 }] }
    expect(computeDropIndex(5, 30, oneRowGeom, 'one')).toBe(0)
    expect(computeDropIndex(50, 30, oneRowGeom, 'one')).toBe(1)
    expect(computeDropIndex(150, 30, oneRowGeom, 'one')).toBe(3)
  })

  it('无几何或空行 → null', () => {
    expect(computeDropIndex(10, 10, null, 'two')).toBe(null)
    expect(computeDropIndex(10, 10, { half: 0, rows: [] }, 'two')).toBe(null)
  })
})
