// 复盘进度条：嵌在复盘控制卡的「进度文字行」位置，替代原来的「第 X/Y 手 · 播放状态」。
// 无外层背景/边框（纯滑块 + 上一轮/下一轮 + 进度文字），不再浮动遮挡手牌。
// 纯展示组件，只依赖 props：
//   currentIndex / total      —— range 的值与范围（history 长度）
//   progressText / statusText —— 「第 x/total 手」+ 播放状态
//   onScrub(idx, mode)        —— exact 拖条 / skip 下一轮 / prev 上一轮
import React from 'react'

const ReplayProgress = ({ currentIndex, total, progressText, statusText, onScrub }) => {
  const value = Number.isFinite(currentIndex) && currentIndex >= 0 ? currentIndex : 0;
  const max = Math.max(0, total - 1);

  return (
    <div className="flex items-center gap-2 sm:gap-3 w-full">
      <button
        type="button"
        onClick={() => onScrub && onScrub(0, "prev")}
        className="flex-shrink-0 px-2.5 sm:px-3 py-1 rounded-full bg-slate-700 hover:bg-slate-600 text-slate-100 text-xs sm:text-sm font-semibold transition-colors"
        aria-label="上一轮出牌"
      >
        ◀ 上一轮
      </button>

      <input
        type="range"
        min="0"
        max={max}
        value={Math.min(value, max)}
        step="1"
        onChange={(e) => onScrub && onScrub(Number(e.target.value), "exact")}
        className="flex-1 min-w-0 h-1.5 accent-amber-400 cursor-pointer"
        aria-label="复盘进度"
      />

      <span className="flex-shrink-0 text-[10px] sm:text-xs text-slate-300 whitespace-nowrap tabular-nums">
        {progressText || `第 ${value + 1}/${total} 手`}
      </span>

      <button
        type="button"
        onClick={() => onScrub && onScrub(value + 1, "skip")}
        className="flex-shrink-0 px-2.5 sm:px-3 py-1 rounded-full bg-slate-700 hover:bg-slate-600 text-slate-100 text-xs sm:text-sm font-semibold transition-colors"
        aria-label="下一轮出牌"
      >
        下一轮 ▶
      </button>

      {statusText && (
        <span className="hidden md:inline flex-shrink-0 text-[10px] sm:text-xs text-slate-400 whitespace-nowrap">
          {statusText}
        </span>
      )}
    </div>
  );
};

export default ReplayProgress
