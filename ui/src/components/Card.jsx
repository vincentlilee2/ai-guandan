// game/ui/src/components/Card.jsx
import React from 'react';

// 定义花色对应的字符和颜色样式
const suitSymbols = {
  'H': { char: '♥', color: 'text-red-500' }, // 红桃 (Hearts)
  'D': { char: '♦', color: 'text-red-500' }, // 方片 (Diamonds)
  'S': { char: '♠', color: 'text-black' },   // 黑桃 (Spades)
  'C': { char: '♣', color: 'text-black' },   // 梅花 (Clubs)
  'J': { char: 'JK', color: 'text-purple-600' } // Joker (王)
};

// 定义点数映射 (11->J, 15->2, 20->小王)
const rankMap = {
  11: 'J', 
  12: 'Q', 
  13: 'K', 
  14: 'A', 
  15: '2', 
  20: '小王', 
  21: '大王'
};

export default function Card({ id, selected, onClick, scale = 1.0, cornerScale = 1, cornerTextClass, cornerOffsetClass, cornerSuitOffsetClass, centerOffsetClass }) {
  // 解析 ID，例如 "H2-0", "S10-1", "J21-0"
  // 格式: {Suit}{Rank}-{DeckIndex}
  // 1. 先去掉后面的 -0, -1
  const rawId = id.split('-')[0]; 
  
  // 2. 提取花色 (第一个字符)
  const suitChar = rawId[0];
  
  // 3. 提取数字 (从第二个字符开始)
  const rankVal = parseInt(rawId.substring(1));
  
  // 获取样式配置
  const suit = suitSymbols[suitChar] || { char: '?', color: 'text-gray-500' };
  const rankStr = rankMap[rankVal] || rankVal; // 如果映射里没有，就直接显示数字(如3-10)
  
  // 处理选中状态的样式 (上浮 + 黄色边框 + 发光)
  const selectedClass = selected 
    ? "-translate-y-4 border-yellow-400 ring-2 ring-yellow-400 card-selected"
    : "hover:-translate-y-2 hover:shadow-lg border-gray-300";

  
  // 如果 scale 小，就不显示 hover 效果，也不响应点击
  const isSmall = scale < 0.8;
  const containerClass = isSmall 
    ? `rounded shadow card-compact border border-gray-200 ${selected ? 'card-selected' : ''}`
    : `rounded-lg card-elevated border-2 cursor-pointer transition-all duration-200 ${selectedClass}`;

  return (
    <div 
      onClick={() => !isSmall && onClick(id)}
      style={{ width: isSmall ? `${20 * 4 * scale}px` : undefined, height: isSmall ? `${28 * 4 * scale}px` : undefined }}
      className={`
        relative select-none flex flex-col items-center justify-center
        ${isSmall ? '' : 'w-20 h-28'}
        ${containerClass}
      `}
    >
      {/* 左上角小标 */}
      <div
        className={`absolute top-1 left-1 ${cornerTextClass || (isSmall ? 'text-sm sm:text-[10px]' : 'text-base')} ${cornerOffsetClass || ''} font-bold ${suit.color} flex flex-col items-center leading-none`}
        style={{ transform: `scale(${cornerScale})`, transformOrigin: 'top left' }}
      >
        <span>{rankStr}</span>
        <span className={cornerSuitOffsetClass || ''}>{suit.char}</span>
      </div>

      {/* 中间大图标 */}
      <div className={`card-center-suit ${isSmall ? 'text-2xl sm:text-xl' : 'text-5xl'} ${centerOffsetClass || ''} ${suit.color} font-semibold ${isSmall ? '' : 'mt-1'}`}>
        {suit.char}
      </div>

      {/* 右下角倒置小标 (为了更像真牌) */}
      {!isSmall && (
        <div className={`absolute bottom-1 right-1 text-sm font-bold ${suit.color} flex flex-col items-center leading-none rotate-180`}>
          <span>{rankStr}</span>
          <span>{suit.char}</span>
        </div>
      )}
    </div>
  );
}