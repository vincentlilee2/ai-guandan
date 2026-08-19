// 3.1 拆分：从 App.jsx 抽出的展示组件（复盘手牌条）
// 仅依赖 props + <SimpleCard>，无 App 状态。
import React from 'react'
import SimpleCard from './SimpleCard'

const ReplayHandStrip = ({ cards = [], variant = "top", vertical = false, compact = false }) => {
  if (!cards || cards.length === 0) {
    return <div className="text-xs text-slate-400">已出完</div>;
  }

  // 根据玩家设置不同的主题颜色
  const getTheme = () => {
    if (variant === "top") return { theme: "blue", label: "对家", bg: "bg-blue-900/75", border: "border-blue-700/60" };
    if (variant === "bottom") return { theme: "green", label: "我方", bg: "bg-green-900/75", border: "border-green-700/60" };
    if (variant === "left") return { theme: "orange", label: "左家", bg: "bg-orange-900/75", border: "border-orange-700/60" };
    if (variant === "right") return { theme: "red", label: "右家", bg: "bg-red-900/75", border: "border-red-700/60" };
    return { theme: "default", label: "", bg: "bg-slate-900/60", border: "border-slate-600/50" };
  };

  const { theme, label, bg, border } = getTheme();
  const themeColor = theme === "blue" ? "text-blue-400" : theme === "red" ? "text-red-400" : theme === "green" ? "text-green-400" : theme === "orange" ? "text-orange-400" : "text-slate-300";

  // 纵向多列（复盘移动端左右家手牌）：每张牌正常大小，利用屏幕高度分列显示。
  // compact：该侧刚出牌（气泡在屏）时，纵向行数更多、行高更矮，整体更短，
  // 配合外层的上移（top-[10%]）让手牌条避开被自己的最新出牌气泡遮挡。
  if (vertical) {
    const cardsPerCol = compact
      ? Math.max(2, Math.ceil(cards.length / 9))
      : Math.max(4, Math.ceil(cards.length / 5));
    const maxH = compact ? 'max-h-[18vh]' : 'max-h-[70vh]';
    const cols = [];
    for (let i = 0; i < cards.length; i += cardsPerCol) {
      cols.push(cards.slice(i, i + cardsPerCol));
    }
    return (
      <div className={`flex flex-row items-start gap-0.5 px-1 py-1 ${bg} border ${border} rounded shadow-sm`}>
        <div className="text-[8px] sm:text-[9px] text-slate-300 font-medium whitespace-nowrap flex-shrink-0">
          {label} · {cards.length}张
        </div>
        <div className={`flex flex-row items-start ${maxH} overflow-y-auto`}>
          {cols.map((col, colIdx) => (
            <div key={colIdx} className="flex flex-col items-center gap-1.5 sm:gap-2">
              {col.map((cid, idx) => (
                <SimpleCard key={`${variant}-${cid}-${idx}`} id={cid} theme={theme} />
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }

  // 横向（默认）：分组显示，避免过长
  const cardsPerRow = variant === "top" || variant === "bottom" ? 22 : 5;
  const rows = [];
  for (let i = 0; i < cards.length; i += cardsPerRow) {
    rows.push(cards.slice(i, i + cardsPerRow));
  }

  return (
    <div className={`flex flex-col items-center gap-0.5 px-1 py-0.5 sm:py-1 ${bg} border ${border} rounded shadow-sm w-full`}>
      <div className="text-[8px] sm:text-[9px] text-slate-300 font-medium whitespace-nowrap flex-shrink-0">
        {label} · {cards.length}张
      </div>
      {rows.map((row, rowIdx) => (
        <div key={rowIdx} className="flex flex-wrap justify-center items-center gap-0 leading-none flex-shrink-0">
          {row.map((cid, idx) => (
            <SimpleCard key={`${variant}-${cid}-${idx}`} id={cid} theme={theme} />
          ))}
        </div>
      ))}
    </div>
  );
};

export default ReplayHandStrip
