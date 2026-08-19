// 3.1 拆分：从 App.jsx 抽出的展示组件（出牌气泡）
// 仅依赖 props + <Card>，无 App 状态。
import React, { useState, useEffect } from 'react'
import Card from './Card'

const PlayedCardBubble = ({ moves = [], layout = "horizontal", position = "bottom" }) => {
  // 注意：所有 Hook 必须在任何 early return 之前调用（React Hooks 规则）
  // 记录已经播放过抖动动画的 PASS key（用 state 而非 ref：
  // React Compiler 规则禁止在 render 期读写 ref）
  const [shakenPassKey, setShakenPassKey] = useState(null);

  const isPassMove = (moveData) => {
    const isString = typeof moveData === 'string';
    const action = isString ? (moveData === "Pass" ? "PASS" : "PLAY") : moveData.action;
    const desc = isString ? moveData : moveData.desc;
    return action === "PASS" || desc === "PASS" || desc === "Pass" || desc === "不出";
  };
  // 只显示最近 3 条；PASS 仅保留最新一条，避免上一轮的暗色 PASS 继续显示
  const safeMoves = moves || [];
  const firstPassIndex = safeMoves.findIndex(isPassMove);
  const filteredMoves = safeMoves.filter(
    (moveData, index) => !isPassMove(moveData) || index === firstPassIndex
  );
  const displayMoves = filteredMoves.slice(0, 3);
  const newestPass = displayMoves.find(isPassMove);
  const newestPassKey = newestPass
    ? (typeof newestPass === 'string'
      ? `pass:${newestPass}`
      : `pass:${newestPass.action || ''}|${newestPass.desc || ''}|${(newestPass.cards || []).join(',')}|${newestPass.ts || ''}`)
    : null;
  // 新的 PASS 出现时抖动一次：key 变化即触发，动画播完由 state 标记为已播放
  const shouldShakeNewestPass = Boolean(newestPassKey) && newestPassKey !== shakenPassKey;
  // 抖动动画播放一次后记录该 key，避免后续重渲染反复抖动
  useEffect(() => {
    if (!newestPassKey || newestPassKey === shakenPassKey) return;
    const timer = setTimeout(() => setShakenPassKey(newestPassKey), 600);
    return () => clearTimeout(timer);
  }, [newestPassKey, shakenPassKey]);


  if (!moves || moves.length === 0) return <div className="h-12 w-1"></div>;


  const isVertical = layout === "vertical";

  // 使用 Grid 布局来实现绝对堆叠，防止撑开页面高度
  const containerClass = "grid grid-cols-1 grid-rows-1 items-center justify-items-center overflow-visible";

  return (
    <div className={containerClass}>
      {displayMoves.map((moveData, idx) => {
        // 兼容旧数据格式 (如果是字符串)
        const isString = typeof moveData === 'string';
        const action = isString ? (moveData === "Pass" ? "PASS" : "PLAY") : moveData.action;
        const desc = isString ? moveData : moveData.desc;
        const cards = isString ? [] : (moveData.cards || []);

        const isPass = action === "PASS" || desc === "PASS" || desc === "Pass" || desc === "不出";

        // z-index: 最新的(idx=0)最高，依次降低
        const zIndex = 30 - idx * 10;
        const opacityClass = idx === 0 ? "opacity-100 scale-100" : "opacity-60 scale-90 grayscale";

        // 计算堆叠偏移 (Grid布局下使用 transform)
        // idx=0: 无偏移
        // idx>0: 向后/向上偏移
        let transformStyle = {};
        const offset = 40 * idx;
        if (idx > 0) {
           if (isVertical) {
              // 垂直布局：区分左右
              // LeftBot (position="left"): 移动端头像是Top，Cards在Bottom。为了不遮挡头像，历史牌应向下偏移 (Positive Y)
              // RightBot (position="right"): 移动端头像是Bottom，Cards在Top。为了不遮挡头像，历史牌应向上偏移 (Negative Y)
              // 注意：这里使用的是 transform，所以是相对位移
              const dir = position === "left" ? 1 : -1;
              transformStyle = { transform: `translateY(${dir * offset}px) scale(${1 - idx * 0.05})` };
           } else {
              // 水平布局：旧牌向左偏移 (显示在后面)
              transformStyle = { transform: `translateX(-${offset}px) scale(${1 - idx * 0.05})` };
           }
        }

        // 统一 Grid 区域
        const gridStyle = { gridArea: '1 / 1 / 2 / 2', zIndex, ...transformStyle };

        if (isPass) {
          const passLabel = (() => {
            if (desc && desc !== "PASS" && desc !== "Pass") return desc;
            return "PASS";
          })();
          const passStyles = {
            ...gridStyle,
            zIndex: idx === 0 ? gridStyle.zIndex + 300 : gridStyle.zIndex
          };
          const shakeClass = idx === 0 && shouldShakeNewestPass ? "pass-shake" : "";
          return (
            <div
              key={idx}
              style={passStyles}
              className={`${isVertical
                ? 'px-2 py-4 rounded-lg [writing-mode:vertical-rl]'
                : 'px-5 py-1.5 rounded-full'} border-2 border-amber-400 bg-black/80 text-amber-200 font-bold tracking-widest uppercase shadow-lg shadow-amber-900/30 ${opacityClass} ${shakeClass}`}
            >
              {passLabel}
            </div>
          );
        }

        // 如果有具体的牌ID，显示牌面
        if (cards.length > 0) {
           return (
             <div key={idx} style={gridStyle} className={`flex items-center justify-center ${opacityClass}`}>
               <div className={`flex ${isVertical ? 'flex-col' : 'flex-row'} overflow-visible`} style={{ marginLeft: isVertical ? '0' : '20px', marginTop: isVertical ? '20px' : '0' }}>
                 {cards.map((cid, cIdx) => {
                   // 根据位置决定旋转角度
                   let rotateStyle = {};
                   if (position === "left") rotateStyle = { transform: "rotate(90deg)" };
                   if (position === "right") rotateStyle = { transform: "rotate(-90deg)" };

                   // 修复右侧玩家牌面数字被遮挡的问题
                   const baseZ = position === "right" ? (50 - cIdx) : cIdx;
                   // 长牌扇（5 张以上）可能向下延伸进下方操作按钮区。出牌位 z=35~60 高于
                   // 按钮区 z=20，若不穿透点击，左家竖排长牌扇会挡住「不出/出牌」按钮。
                   // 出牌位纯展示，按钮点击无需经过它 → 左家也给穿透，统一行为。
                   const isVerticalLong = isVertical && position === "left" && cards.length >= 5;

                   return (
                     <div
                        key={cid}
                        className={`transition-all ${isVertical ? '-mt-8' : '-ml-5'} ${isVerticalLong ? 'pointer-events-none' : ''}`}
                        style={{...rotateStyle, zIndex: baseZ}}
                     >
                        <Card
                          id={cid}
                          selected={false}
                          onClick={() => {}}
                          scale={0.7}
                          cornerTextClass="text-sm sm:text-xl"
                          cornerOffsetClass="sm:-translate-y-2"
                          cornerSuitOffsetClass="sm:-mt-2 sm:text-lg"
                          centerOffsetClass="sm:translate-x-2 sm:translate-y-4 sm:text-4xl"
                        />
                     </div>
                   );
                 })}
               </div>
             </div>
           );
        }

        // 降级显示文本 (如果没有牌ID)
        const isBomb = desc && (desc.includes("炸") || desc.includes("天王"));
        let colorClass = "bg-slate-700 text-white border-slate-500";
        if (isBomb) colorClass = "bg-yellow-900/80 text-yellow-300 border-yellow-500 shadow-yellow-500/20";

        return (
          <div key={idx} style={gridStyle} className={`px-4 py-2 rounded-xl border-2 shadow-lg backdrop-blur-sm ${colorClass} ${opacityClass}`}>
            <span className="font-bold text-sm whitespace-nowrap">{desc}</span>
          </div>
        );
      })}
    </div>
  );
};

export default PlayedCardBubble
