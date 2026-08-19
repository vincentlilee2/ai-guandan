// 3.1 拆分：从 App.jsx 抽出的玩家手牌区（HandCards）
// 纯展示 + 本地命中检测 + 拖拽组排；所有数据/回调由父级以 props 传入，不持有 App 状态。
import React, { useLayoutEffect, useRef, useState } from 'react'
import Card from './Card'
import { cardsWithKeys, computeRowSplit } from '../lib/handOrder'
import { useHandDrag } from '../hooks/useHandDrag'

// 移动端两行卡片按可用高度缩放：
// 底部区域（flex-1）位于固定高的上家 + 牌桌之下，其「顶部到视口底边」的高度
// 随视口变矮而变小。区域内手牌上/下的固定占用（出牌气泡 + 按钮行 + 「已选」行 + 底边）
// ≈ ABOVE_HAND_BUDGET，剩余空间分给手牌；不足时把整副手牌（卡片布局缩放 + 行叠放 +
// 弧形 + 内边距）等比缩小，保证任何视口下手牌不溢出、不被底边裁掉、不遮挡出牌按钮。
const ABOVE_HAND_BUDGET = 116 // 出牌气泡(~50) + 按钮行(~42) + 「已选」行(16) + 底边留白(8)
const MIN_RATIO = 0.4
const ROW_PITCH = 48 // 两行卡片的视觉行距（原 -mt-16 下 112px 行的真实间距）

const HandCards = ({
  cards = [],
  selected = [],
  handDisplayParams = { scale: 0.7, overlap: '-50px', paddingLeft: '32px' },
  isReplayOpen = false,
  canDrag = true,
  onToggleCard,
  onReorder,
  cardHitRefs,   // ref 指向 Map: lookupKey -> DOM 节点
  registerCardHit, // (lookupKey, node) => void
}) => {
  if (isReplayOpen) return null;

  // 移动端（两行）缩放因子：可用空间不足时收紧（等比缩放卡片+行叠放）。
  const mobileBoxRef = useRef(null)
  const [fitRatio, setFitRatio] = useState(1)
  const fitRatioRef = useRef(1)  // 供量测读取最新值，避免 effect 因 fitRatio 反复重建
  fitRatioRef.current = fitRatio
  const naturalHRef = useRef(0)  // fitRatio=1 时盒子自然高度（含旋转/弧形包围盒），首帧后固定
  const lastScaleRef = useRef(null) // 记录量到自然高度时的基础缩放，断点变化时重置

  // 用 useLayoutEffect：在浏览器绘制前量测并设置 fitRatio，避免首帧满尺寸闪烁。
  useLayoutEffect(() => {
    const el = mobileBoxRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    // 自然高度（fitRatio=1 时盒子的真实高度）先按「盒高 ÷ 当前 fitRatio」反推，
    // 首帧之后便收敛为常数；旋转/弧形的包围盒已计入盒高，无需手工建模。
    const measure = () => {
      if (!el.querySelectorAll('[data-key]').length) return
      const clientH = document.documentElement.clientHeight || window.innerHeight
      if (!naturalHRef.current || lastScaleRef.current !== handDisplayParams.scale) {
        const rect = el.getBoundingClientRect()
        const cur = fitRatioRef.current || 1
        naturalHRef.current = rect.height > 0 ? rect.height / cur : 1
        lastScaleRef.current = handDisplayParams.scale
      }
      // 底部区域顶 = mobileBox 一路向上的最近 flex-1 祖先。区域顶位于固定高的
      // 上家+牌桌之下，不随手牌/按钮位置变化 → 预算稳定，无反馈循环。
      let regionTop = el.getBoundingClientRect().top
      let p = el.parentElement
      while (p && !(p.className.includes('flex-1') || p.style.flex === '1')) p = p.parentElement
      if (p) regionTop = p.getBoundingClientRect().top
      // 手牌可用高度 = 视口底边 − 区域顶 − 区域内手牌上下固定占用
      const budget = Math.max(0, clientH - regionTop - ABOVE_HAND_BUDGET)
      const ratio = Math.min(1, Math.max(MIN_RATIO, budget / naturalHRef.current))
      setFitRatio((prev) => (Math.abs(prev - ratio) > 0.01 ? ratio : prev))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    window.addEventListener('resize', measure)
    const vv = window.visualViewport
    if (vv) vv.addEventListener('resize', measure)
    window.addEventListener('orientationchange', measure)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', measure)
      if (vv) vv.removeEventListener('resize', measure)
      window.removeEventListener('orientationchange', measure)
    }
  }, [handDisplayParams.scale, cards.length])

  // 应用到卡片的缩放 = 基础缩放 × 空间收紧比例；行距/弧形/内边距按 fitRatio 同缩，
  // 使整副手牌的布局高度与卡片视觉同步缩小（行距=行高−ROW_PITCH 保持原叠放比例）。
  const cardScale = (handDisplayParams.scale || 0.7) * fitRatio
  const cardH = 112 * cardScale
  const rowOverlap = Math.max(0, Math.round(cardH - ROW_PITCH * fitRatio))
  // 卡片布局盒宽 = 80×scale（完整牌面 w-20 从底部中心缩放后的盒宽）。
  // 横向叠放要保持「每张卡净推进」与原来一致（原 80px 布局卡 -58px 叠放 → 净推进 22px），
  // 因此 marginLeft = -(卡片盒宽 − 原净推进×fitRatio)，盒宽随 scale 变窄时净推进不变。
  const cardW = 80 * cardScale
  const pitchBase = 80 + (parseInt(handDisplayParams.overlap, 10) || -58) // 每张卡净推进(px)，如 22
  const overlapPx = Math.max(0, Math.round(cardW - pitchBase * fitRatio))
  const pb = Math.round(10 * fitRatio)         // 原 pb-2.5

  // 稳定唯一 key（防重复 id）+ 扁平顺序
  const keyed = cardsWithKeys(cards);
  const half = computeRowSplit(keyed.length);

  // 移动端（两行）与桌面端（单行）各一份拖拽逻辑。
  // 两布局共用同一 cardHitRefs Map，用前缀隔离命中键（屏幕同一时刻只显示一个布局）。
  const mobileDrag = useHandDrag({
    layout: 'two',
    cards,
    selected,
    cardHitRefs,
    refPrefix: 'm:',
    canDrag,
    onReorder,
    onToggleCard,
  });
  const desktopDrag = useHandDrag({
    layout: 'one',
    cards,
    selected,
    cardHitRefs,
    refPrefix: 'd:',
    canDrag,
    suppressTapToggle: true, // 桌面 tap 由 Card onClick 处理，避免双重触发
    onReorder,
    onToggleCard,
  });

  const rowsArr = [keyed.slice(0, half), keyed.slice(half)];

  const rowZ = (rowIndex, index) => {
    const item = rowsArr[rowIndex]?.[index];
    const isSel = item ? selected.includes(item.id) : false;
    return isSel ? 999 : rowIndex * 100 + index;
  };

  return (
    <div className="w-full flex justify-center px-2 sm:px-4">
      {/* 小屏：两行叠牌（弧形排列，精确触摸热区）
          mobileBoxRef：以手牌区顶部为锚，量测可用高度 → 收紧 fitRatio，
          卡片缩放/行叠放/弧形/内边距等比缩小，保证不溢出屏幕底边。 */}
      <div ref={mobileBoxRef} className="sm:hidden w-full flex flex-col items-center" style={{ paddingLeft: handDisplayParams.paddingLeft, paddingRight: '8px' }}>
        <div
          className="w-full flex flex-col items-center relative"
          style={{ paddingBottom: pb }}
          {...mobileDrag.handlers}
        >
          {rowsArr.map((row, rowIndex) => {
            if (row.length === 0) return null;
            return (
              <div
                key={rowIndex}
                data-row={rowIndex}
                className="flex items-end w-full"
                style={{ justifyContent: 'center', marginTop: rowIndex > 0 ? `-${rowOverlap}px` : undefined }}
              >
                {row.map((item, index) => {
                  const isSelected = selected.includes(item.id);
                  const rowLength = row.length;
                  const centerIndex = (rowLength - 1) / 2;
                  const rotationAngle = (index - centerIndex) * 2.5;
                  const distanceFromCenter = Math.abs(index - centerIndex);
                  const arcHeight = distanceFromCenter * 2 * fitRatio;
                  const cardZIndex = rowZ(rowIndex, index);
                  const isDragging = mobileDrag.dragState?.key === item.key;

                  return (
                    <div
                      key={item.key}
                      data-key={item.key}
                      className="relative"
                      style={{
                        marginLeft: index === 0 ? 0 : `-${overlapPx}px`,
                        zIndex: isDragging ? 1000 : cardZIndex,
                        transform: isDragging
                          ? `translate(${mobileDrag.dragState.dx}px, ${mobileDrag.dragState.dy}px)`
                          : `translateY(${arcHeight}px)`,
                        transition: isDragging ? 'none' : undefined,
                        touchAction: 'manipulation',
                        opacity: mobileDrag.dragState && !isDragging ? 0.4 : 1,
                        pointerEvents: mobileDrag.dragState && !isDragging ? 'none' : undefined,
                      }}
                    >
                      {/* 恢复原牌面：外层盒子按 cardW×cardH 定布局尺寸（随 fitRatio 收紧，
                          与紧凑卡的布局高度行为一致），内层以完整尺寸牌面（w-20 h-28）从
                          底部中心 scale 到 cardScale —— 布局不变，牌面回到原版大字角标/
                          居中大花色的样式。 */}
                      <div className="relative flex items-end justify-center" style={{ width: cardW, height: cardH }}>
                        <div
                          className="origin-bottom"
                          style={{ transform: `scale(${cardScale}) rotate(${rotationAngle}deg)` }}
                          ref={(node) => registerCardHit(`m:${item.key}`, node)}
                        >
                          <Card id={item.id} selected={isSelected} onClick={() => {}} cornerScale={1.3} />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })}
          {/* 移动端 drop 指示（竖直条，fixed 定位免于祖先 transform 影响） */}
          {mobileDrag.dropBar && (
            <div
              data-dropbar
              className="fixed pointer-events-none bg-yellow-400 rounded w-1.5"
              style={{
                left: mobileDrag.dropBar.left - 3,
                top: mobileDrag.dropBar.top,
                height: mobileDrag.dropBar.height,
                zIndex: 2000,
              }}
            />
          )}
        </div>
      </div>

      {/* 中大屏：堆叠 + hover 展开 */}
      <div
        className={`hidden sm:flex items-end justify-center h-28 pl-8 min-w-max ${desktopDrag.dragState ? 'dragging' : ''}`}
        data-layout="desktop"
        {...desktopDrag.handlers}
      >
        {keyed.map((item, index) => {
          const isSelected = selected.includes(item.id);
          const isDragging = desktopDrag.dragState?.key === item.key;
          return (
            <div
              key={item.key}
              data-key={item.key}
              className={`relative ${isDragging ? 'z-50' : 'z-0'} ${
                desktopDrag.dragState
                  ? ''
                  : '-ml-12 hover:-ml-2 hover:mr-8 transition-all duration-200 ease-out origin-bottom hover:-translate-y-4 cursor-pointer hover:z-50'
              }`}
            >
              <div
                className="origin-bottom scale-100"
                ref={(node) => registerCardHit(`d:${item.key}`, node)}
              >
                <Card id={item.id} selected={isSelected} onClick={onToggleCard} cornerScale={1.3} />
              </div>
            </div>
          );
        })}
        {desktopDrag.dropBar && (
          <div
            data-dropbar
            className="fixed pointer-events-none bg-yellow-400 rounded w-1.5"
            style={{
              left: desktopDrag.dropBar.left - 3,
              top: desktopDrag.dropBar.top,
              height: desktopDrag.dropBar.height,
              zIndex: 2000,
            }}
          />
        )}
      </div>
    </div>
  );
};

export default HandCards
