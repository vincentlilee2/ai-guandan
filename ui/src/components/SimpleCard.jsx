// game/ui/src/components/SimpleCard.jsx
// 简化版卡牌组件，用于复盘界面，只显示花色和数字，无背景
import React from 'react';

const suitSymbols = {
  'H': { char: '♥', color: 'text-red-500' },
  'D': { char: '♦', color: 'text-red-500' },
  'S': { char: '♠', color: 'text-gray-800' },
  'C': { char: '♣', color: 'text-gray-800' },
  'J': { char: 'JK', color: 'text-purple-600' }
};

const rankMap = {
  11: 'J', 12: 'Q', 13: 'K', 14: 'A', 15: '2',
  20: '小王', 21: '大王'
};

export default function SimpleCard({ id, theme = "default" }) {
  const rawId = id.split('-')[0]; 
  const suitChar = rawId[0];
  const rankVal = parseInt(rawId.substring(1));
  
  const suit = suitSymbols[suitChar] || { char: '?', color: 'text-gray-500' };
  const rankStr = rankMap[rankVal] || rankVal;
  
  // 根据主题设置颜色
  const getThemeColor = () => {
    switch(theme) {
      case "blue": return "text-blue-400";
      case "red": return "text-red-400";
      case "green": return "text-green-400";
      case "orange": return "text-orange-400";
      default: return suit.color;
    }
  };
  
  const themeColor = getThemeColor();
  
  // 特殊处理：大小王只显示文字
  if (suitChar === 'J') {
    return (
      <div className="inline-flex flex-col items-center justify-center min-w-[26px] sm:min-w-[32px] px-0.5">
        <span className={`text-sm sm:text-base font-extrabold ${themeColor} leading-tight`}>
          {rankStr}
        </span>
      </div>
    );
  }
  
  return (
    <div className="inline-flex flex-col items-center justify-center min-w-[26px] sm:min-w-[32px] px-0.5">
      <span className={`text-lg sm:text-xl font-extrabold ${themeColor} leading-none`}>
        {suit.char}
      </span>
      <span className={`text-xs sm:text-sm font-extrabold ${themeColor} leading-tight`}>
        {rankStr}
      </span>
    </div>
  );
}
