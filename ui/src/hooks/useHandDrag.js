// 手牌拖拽组排（v3.2）：Pointer Events 拖拽状态机。
// 用 6px 移动阈值区分「点选」与「拖拽」：
//  - 移动端（layout='two'，容器 handler + setPointerCapture）：tap → onToggleCard
//  - 桌面端（layout='one'，容器 handler，不 capture）：tap 由 Card 自带 onClick 处理（suppressTapToggle）
// 拖拽期间挂 window-level pointermove/up/cancel，保证指针移出容器仍能跟踪。
// 纯几何计算（measureGeometry/computeDropIndex）导出，便于无 DOM 单测。
import { useRef, useState, useCallback, useEffect } from 'react'
import { cardsWithKeys, computeRowSplit, rowSlotToFlatIndex } from '../lib/handOrder'

const MOVE_THRESHOLD = 6 // px，超过即视为拖拽
// 判定为拖拽的兜底：即使移动超过阈值，若距离起点仍在 10px 内，
// 也视为点选而非拖拽 —— 容忍指尖轻微抖动（真实用户几乎不会在拖 1 张牌
// 到新位置时只移动 10px）。修复「抖动 tap 被吞」。不用起始牌 rect 判断：
// 牌扇 rect 几乎覆盖整副手牌，用它会误把 47px 的跨行拖拽当成「还在起始牌上」。
const TAP_HOLD_BACKFILL_RADIUS = 10 // px

export function useHandDrag({
  layout,             // 'two' | 'one'
  cards,              // 扁平 id 数组（当前展示顺序）
  selected = [],      // 选中 id 数组（重叠区 z-index 判定）
  cardHitRefs,        // { current: Map<lookupKey, DOM节点> }
  refPrefix = '',     // 命中键前缀（移动/桌面共用同一 Map 时隔离）
  canDrag,
  suppressTapToggle = false, // true 时 tap 不回调 onToggleCard（桌面由 Card onClick 处理）
  onReorder,          // (from, to) 扁平索引
  onToggleCard,       // (id)
  onDragStateChange,  // (bool) 可选
}) {
  const [dragState, setDragState] = useState(null) // { key, dx, dy }
  const [dropIndex, setDropIndex] = useState(null)
  const [dropBar, setDropBar] = useState(null)     // { left, top, height } client 坐标
  const dragRef = useRef(null)                     // { from, key, startX, startY, moved }
  const geometryRef = useRef(null)
  const windowListenersRef = useRef(null)          // 当前挂载的 window 监听器
  // dropIndex 在 window 监听器里更新，up 时需读最新值 → 用 ref 镜像
  const dropIndexRef = useRef(null)

  const latest = useRef({ layout, cards, selected, canDrag, suppressTapToggle, onReorder, onToggleCard, onDragStateChange })
  latest.current = { layout, cards, selected, canDrag, suppressTapToggle, onReorder, onToggleCard, onDragStateChange }

  const removeWindowListeners = useCallback(() => {
    const wl = windowListenersRef.current
    if (!wl) return
    window.removeEventListener('pointermove', wl.move)
    window.removeEventListener('pointerup', wl.up)
    window.removeEventListener('pointercancel', wl.cancel)
    windowListenersRef.current = null
  }, [])

  const clearDrag = useCallback(() => {
    dragRef.current = null
    geometryRef.current = null
    setDragState(null)
    setDropIndex(null)
    setDropBar(null)
    latest.current.onDragStateChange?.(false)
  }, [])

  // 同步挂载 capture 期 click 抑制器：拖拽结束同一任务内浏览器会补发合成 click
  //（同一 task 先于任何 macrotask），必须在 render 前就捕获它。若合成 click 在
  // 其它元素上（拖到空白处释放），macrotask 兜底自动卸载，避免吞掉用户下一次点选。
  const armClickSuppressor = useCallback(() => {
    const handler = (e) => {
      e.preventDefault()
      e.stopPropagation()
      window.removeEventListener('click', handler, true)
    }
    window.addEventListener('click', handler, true)
    setTimeout(() => {
      window.removeEventListener('click', handler, true)
    }, 0)
  }, [])

  const endGesture = useCallback(({ reorder }) => {
    const d = dragRef.current
    if (d && d.moved) {
      const idx = dropIndexRef.current
      if (reorder && idx !== null && idx !== d.from) {
        latest.current.onReorder?.(d.from, idx)
      }
      // 仅桌面需要抑制拖拽伴随的合成 click（桌面卡 onClick 会误触 toggle）。
      // 移动端卡 onClick 是 no-op，合成 click 无害 → 不抑制，避免泄漏吞掉下一次点选。
      if (latest.current.layout === 'one') {
        armClickSuppressor()
      }
    }
    removeWindowListeners()
    clearDrag()
  }, [clearDrag, removeWindowListeners, armClickSuppressor])


  const onWindowMove = useCallback((e) => {
    const d = dragRef.current
    if (!d) return
    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY
    const dist = Math.hypot(dx, dy)
    // 未过阈值 → 仍是 tap 前兆，不处理
    if (!d.moved && dist <= MOVE_THRESHOLD) return
    if (!d.moved) {
      // 过阈值但距起点仍 < 10px → 视为点选前兆（指尖抖动），不进入拖拽。
      // 只有真正拖开才进入拖拽态。
      if (dist <= TAP_HOLD_BACKFILL_RADIUS) return
      d.moved = true
      latest.current.onDragStateChange?.(true)
    }
    setDragState({ key: d.key, dx, dy })
    const l = latest.current
    const idx = computeDropIndex(e.clientX, e.clientY, geometryRef.current, l.layout)
    const isNoOp = idx === null || idx === d.from || idx === d.from + 1
    dropIndexRef.current = isNoOp ? null : idx
    setDropIndex(isNoOp ? null : idx)
    setDropBar(isNoOp ? null : computeDropBar(idx, geometryRef.current, l.layout))
  }, [])

  const onWindowUp = useCallback((e) => {
    const d = dragRef.current
    if (!d) return
    const handleTap = () => {
      // 点选命中按下点（而非抬起点）：抬手位置可能因指尖抖动漂进邻卡重叠区，
      // 用抬起点 hitTest 会选中邻居而非用户真正按下的那张。
      const hit = hitTest(d.startX, d.startY)
      if (hit) {
        e.stopPropagation?.()
        latest.current.onToggleCard?.(hit.item.id)
      }
    }
    if (!d.moved && !latest.current.suppressTapToggle) {
      // 未进入拖拽 → 点选（移动端）
      handleTap()
    } else if (d.moved && !latest.current.suppressTapToggle && distFromStart(e, d) <= TAP_HOLD_BACKFILL_RADIUS) {
      // 已进拖拽态，但指针又回到起始牌附近（<10px）→ 视为点选而非拖拽，
      // 避免「抖一下触发 drag、松开时手指停在牌上」被吞成无声操作。
      dropIndexRef.current = null // 已按点选处理，不再触发 reorder
      handleTap()
    }
    endGesture({ reorder: true })
  }, [endGesture])

  const onWindowCancel = useCallback(() => {
    endGesture({ reorder: false })
  }, [endGesture])

  // hitTest 用最新 cards/selected/cardHitRefs。选中牌上浮 16px 且 z=999（视觉最上层），
  // 命中优先级须与之一致：点选中的牌能直接取消，而不是被相邻牌盖住。
  const hitTest = useCallback((x, y) => {
    const l = latest.current
    const items = cardsWithKeys(l.cards)
    const half = computeRowSplit(items.length)
    const selectedSet = new Set(l.selected)
    let picked = null
    items.forEach((item, idx) => {
      const node = cardHitRefs?.current?.get(`${refPrefix}${item.key}`)
      if (!node) return
      const rect = node.getBoundingClientRect()
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
        const rowIndex = idx < half ? 0 : 1
        const rowIdx = rowIndex === 0 ? idx : idx - half
        const isSelected = selectedSet.has(item.id)
        const zIndex = isSelected ? 999 : rowIndex * 100 + rowIdx
        if (!picked || zIndex > picked.zIndex) {
          picked = { item, idx, zIndex }
        }
      }
    })
    return picked
  }, [cardHitRefs, refPrefix])

  // 挂 window 监听器（一次手势）
  const attachWindowListeners = useCallback(() => {
    removeWindowListeners()
    const wl = { move: onWindowMove, up: onWindowUp, cancel: onWindowCancel }
    windowListenersRef.current = wl
    window.addEventListener('pointermove', wl.move)
    window.addEventListener('pointerup', wl.up)
    window.addEventListener('pointercancel', wl.cancel)
  }, [onWindowMove, onWindowUp, onWindowCancel, removeWindowListeners])

  const onPointerDown = useCallback((e) => {
    if (!latest.current.canDrag) return
    const items = cardsWithKeys(latest.current.cards)
    if (items.length < 1) return
    const hit = hitTest(e.clientX, e.clientY)
    if (!hit) return
    // 单张牌仍须响应点选（否则剩最后一张时无法选中、出牌按钮被 disabled），
    // 但单张无重排可能，跳过几何测量（geometry=null → drop 计算自然返回 null）。
    const isReorderable = items.length >= 2
    dragRef.current = {
      from: hit.idx,
      key: hit.item.key,
      startX: e.clientX,
      startY: e.clientY,
      moved: false,
    }
    geometryRef.current = isReorderable ? measureGeometry(
      latest.current.cards, cardHitRefs, latest.current.layout, refPrefix,
    ) : null
    attachWindowListeners()
  }, [hitTest, cardHitRefs, refPrefix, attachWindowListeners])

  useEffect(() => {
    return () => { removeWindowListeners() }
  }, [removeWindowListeners])

  return {
    dragState,
    dropIndex,
    dropBar,
    handlers: { onPointerDown },
  }
}

// ---------------------------------------------------------------------------
// 以下为纯几何函数（导出便于无 DOM 单测）

// 指针当前相对手势起点的距离
function distFromStart(e, d) {
  return Math.hypot(e.clientX - d.startX, e.clientY - d.startY)
}

// 拖拽开始时测量各卡 rect，推导每行左右边界/上下界/卡中心。
// 两行按 half 拆分；单行整副一行。jsdom 下 rect 为 0 → 跳过（空几何 → drop 计算回退）。
export function measureGeometry(cards, refMap, layout, refPrefix = '') {
  const items = cardsWithKeys(cards)
  const half = layout === 'two' ? computeRowSplit(items.length) : items.length
  const rowKeySets = layout === 'two' ? [items.slice(0, half), items.slice(half)] : [items]
  const rows = rowKeySets.map((rowItems) => {
    let left = Infinity
    let right = -Infinity
    let top = Infinity
    let bottom = -Infinity
    const centers = []
    rowItems.forEach((item) => {
      const node = refMap?.current?.get(`${refPrefix}${item.key}`) || refMap?.get(`${refPrefix}${item.key}`)
      if (!node) return
      const rect = node.getBoundingClientRect()
      if (!rect || rect.width <= 0 || rect.height <= 0) return
      const cx = rect.left + rect.width / 2
      const cy = rect.top + rect.height / 2
      centers.push(cx)
      left = Math.min(left, rect.left)
      right = Math.max(right, rect.right)
      top = Math.min(top, rect.top)
      bottom = Math.max(bottom, rect.bottom)
    })
    if (centers.length === 0) return null
    return { centers, left, right, top, bottom, cy: (top + bottom) / 2 }
  }).filter(Boolean)
  return { rows, half }
}

// 指针 → 扁平插入索引：先按 y 定行（两行以上下行中线为界），再按 x 最近卡中心定行内 slot。
export function computeDropIndex(x, y, geometry, layout) {
  if (!geometry || geometry.rows.length === 0) return null
  const { rows, half } = geometry
  let row = 0
  if (layout === 'two' && rows.length >= 2) {
    row = y < (rows[0].cy + rows[1].cy) / 2 ? 0 : 1
  }
  const r = rows[row]
  if (!r || r.centers.length === 0) return null
  const { centers } = r
  const n = centers.length
  const width = Math.max(1, r.right - r.left)
  const gap = n > 1 ? width / (n - 1) : width
  let slot
  if (x <= r.left + gap / 2) {
    slot = 0
  } else if (x >= r.right - gap / 2) {
    slot = n
  } else {
    let nearest = 0
    let bestDist = Infinity
    centers.forEach((cx, i) => {
      const d = Math.abs(x - cx)
      if (d < bestDist) {
        bestDist = d
        nearest = i
      }
    })
    slot = x < centers[nearest] ? nearest : nearest + 1
  }
  return rowSlotToFlatIndex(row, slot, half)
}

// 插入索引 → 竖直条位置（client 坐标，fixed 定位，免于祖先 transform 影响）。
export function computeDropBar(index, geometry, layout) {
  if (!geometry || index == null || geometry.rows.length === 0) return null
  const { rows, half } = geometry
  let row = 0
  if (layout === 'two') row = index < half ? 0 : 1
  const r = rows[row]
  if (!r || r.centers.length === 0) return null
  const slot = layout === 'two' ? (row === 0 ? index : index - half) : index
  let x
  if (slot <= 0) x = r.left
  else if (slot >= r.centers.length) x = r.right
  else x = (r.centers[slot - 1] + r.centers[slot]) / 2
  return { left: x, top: r.top, height: Math.max(0, r.bottom - r.top) }
}
