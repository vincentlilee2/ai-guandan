// 3.1 拆分：TableBoard —— 主牌桌台面区（三 AI 头像 + 出牌气泡 + 重放条），纯展示组件。
// 机械搬运自 App.jsx:2083-2219，零行为变更；通过 props 透传原 state/ref/handler。
import React from 'react'
import PlayedCardBubble from './PlayedCardBubble'
import ReplayHandStrip from './ReplayHandStrip'

export function TableBoard(props) {
  const {
    activeAiPlayer,
    activeReplayPlayer,
    aiProcessing,
    currentReplayMove,
    displayBotCounts,
    displayHands,
    displayRoundMoves,
    gameState,
    handleAiAvatarClick,
    isReplayOpen,
    leftHasMoves,
    nextReplayMove,
    onAvatarPointerDown,
    onAvatarPointerUp,
    playedBubbleZ,
    replayHeaderLine,
    replayStatusText,
    rightHasMoves,
    statusText,
    totalScores,
    turnStartTime,
    PLAYER_DISPLAY_NAMES,
    SOUND_BASE,
  } = props;

  return (
      <div className="flex-none h-[38vh] sm:h-[40vh] w-full max-w-7xl mx-auto relative px-2 sm:px-3 md:px-4 sm:flex sm:flex-row sm:justify-between sm:items-stretch sm:gap-2">
        
        {/* --- 扑克牌桌背景 --- */}
        <div className="poker-table-container">
          <div className="poker-table"></div>
        </div>

        {/* --- 左家 (Left) --- */}
        {/* 注意：该列有 transform(-translate-y-1/2)，会形成独立 stacking context。
            出牌位的 z-index 必须设在这层（transform 祖先）才能与 User/对家出牌位
            在根 context 直接比较；设在内层 bubble 上会被 transform 困住、永远盖不过上/下家。 */}
        <div className="flex flex-row items-center gap-1 sm:gap-2 w-auto sm:max-w-[35%] justify-start absolute left-4 top-[60%] -translate-y-1/2 sm:static sm:translate-y-0 sm:h-full overflow-visible sm:pl-6"
             style={{ zIndex: playedBubbleZ?.LeftBot }}>
          {/* 复盘模式：仅当轮到左家时显示手牌（桌面端横向，小屏纵向贴边） */}
          {isReplayOpen && activeReplayPlayer === "LeftBot" && displayHands?.LeftBot && (
            <div className="hidden sm:flex flex-col justify-center max-w-[100px] lg:max-w-[140px] flex-shrink-0">
              <ReplayHandStrip cards={displayHands.LeftBot} variant="left" />
            </div>
          )}
          
          <div className="flex flex-col items-center relative z-40">
            {/* 剩余牌数标签 - 移到头像正上方 */}
            <div className={`absolute -top-8 left-1/2 -translate-x-1/2 text-[10px] sm:text-sm px-2 py-0.5 rounded-full border font-bold shadow-lg whitespace-nowrap z-40 ${
              displayBotCounts.LeftBot <= 6 
                ? 'text-red-200 bg-red-900/80 border-red-400/50 animate-pulse' 
                : 'text-blue-200 bg-blue-900/80 border-blue-400/50'
            }`}>
              剩余 {displayBotCounts.LeftBot}张
            </div>
            
            <div className="relative">
              {/* AI思考动态光圈 */}
              {aiProcessing && activeAiPlayer === 'LeftBot' && <div className="ai-thinking-ring"></div>}
              <div 
                onClick={() => handleAiAvatarClick('LeftBot')}
                onPointerDown={() => onAvatarPointerDown('LeftBot')}
                onPointerUp={onAvatarPointerUp}
                onPointerLeave={onAvatarPointerUp}
    // eslint-disable-next-line react-hooks/purity
                className={`w-12 h-12 sm:w-14 sm:h-14 badge-circle badge-red flex items-center justify-center text-base sm:text-lg font-extrabold text-white bg-cover bg-center ${gameState.turn === 'LeftBot' && (Date.now() - turnStartTime > 3000) ? 'cursor-pointer' : 'cursor-help'}`}
                style={{backgroundImage: `url(${SOUND_BASE}avatars/left.jpeg)`, backgroundSize: 'cover', backgroundPosition: 'center'}}
              >
              </div>
            </div>
            <div className="mt-0.5 sm:mt-1 text-xs sm:text-sm font-bold text-yellow-400 drop-shadow-md bg-black/40 px-1.5 sm:px-2 rounded">
              {totalScores.LeftBot}分
            </div>
          </div>
          
          {/* 出牌位（在头像右侧） */}
          <div className="flex justify-start min-w-[60px] sm:min-w-[100px] ml-6 sm:ml-6">
            <div className="flex flex-col items-start gap-1 sm:gap-2 mt-28 sm:mt-28">
              {leftHasMoves && (
                <PlayedCardBubble moves={displayRoundMoves["LeftBot"]} layout="vertical" position="left" />
              )}
            </div>
          </div>
        </div>

        {/* 小屏复盘：左家手牌贴屏幕左缘纵向多列（不挤在中间） */}
        {isReplayOpen && activeReplayPlayer === "LeftBot" && displayHands?.LeftBot && (
          <div className={`sm:hidden absolute left-0 ${leftHasMoves ? 'top-[10%]' : 'top-1/2'} -translate-y-1/2 z-[65] pointer-events-none`}>
            <ReplayHandStrip cards={displayHands.LeftBot} variant="left" vertical compact={leftHasMoves} />
          </div>
        )}

        {/* --- 中央状态提示 --- */}
        <div className={`flex flex-col items-center justify-center ${isReplayOpen ? 'gap-1.5 sm:gap-2' : 'opacity-60 pointer-events-none'} absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 sm:static sm:translate-x-0 sm:translate-y-0 w-[70%] sm:w-auto sm:flex-1 sm:max-w-[30%] text-center z-15`}>
           {isReplayOpen ? (
             <>
               <div className="text-xs sm:text-sm tracking-widest text-gray-400 bg-slate-800/50 px-3 py-1 rounded-full">{replayHeaderLine}</div>
               <div className="text-sm sm:text-base font-bold text-yellow-300 bg-slate-900/70 px-3 sm:px-4 py-2 rounded-lg border border-slate-600">
                 {currentReplayMove
                   ? `${PLAYER_DISPLAY_NAMES[currentReplayMove.player] || currentReplayMove.player}：${currentReplayMove.desc || (currentReplayMove.action === "PASS" ? "PASS" : currentReplayMove.action)}`
                   : replayStatusText || "复盘准备中..."}
               </div>
               {nextReplayMove && (
                 <div className="text-xs text-slate-400 bg-slate-800/30 px-2 py-1 rounded">
                   下一手：{(PLAYER_DISPLAY_NAMES[nextReplayMove.player] || nextReplayMove.player)} · {nextReplayMove.desc || (nextReplayMove.action === "PASS" ? "PASS" : nextReplayMove.action)}
                 </div>
               )}
             </>
           ) : (
             <>
               <div className="text-xs sm:text-sm tracking-widest mb-1 sm:mb-2 text-gray-400">GAME STATUS</div>
               <div className="text-sm sm:text-lg md:text-xl font-semibold text-yellow-400/90">{statusText}</div>
             </>
           )}
        </div>

        {/* --- 右家 (Right) --- */}
        {/* 同左家：transform 祖先处设置 z-index，避免出牌位 z 被 transform 困住 */}
        <div className="flex flex-row-reverse items-center gap-1 sm:gap-2 w-auto sm:max-w-[35%] justify-end absolute right-4 top-[60%] -translate-y-1/2 sm:static sm:translate-y-0 sm:h-full overflow-visible sm:pr-6"
             style={{ zIndex: playedBubbleZ?.RightBot }}>
          {/* 复盘模式：仅当轮到右家时显示手牌（桌面端横向，小屏纵向贴边） */}
          {isReplayOpen && activeReplayPlayer === "RightBot" && displayHands?.RightBot && (
            <div className="hidden sm:flex flex-col justify-center max-w-[100px] lg:max-w-[140px] flex-shrink-0">
              <ReplayHandStrip cards={displayHands.RightBot} variant="right" />
            </div>
          )}
          
          <div className="flex flex-col items-center relative z-40">
            {/* 剩余牌数标签 - 移到头像正上方 */}
            <div className={`absolute -top-8 left-1/2 -translate-x-1/2 text-[10px] sm:text-sm px-2 py-0.5 rounded-full border font-bold shadow-lg whitespace-nowrap z-40 ${
              displayBotCounts.RightBot <= 6 
                ? 'text-red-200 bg-red-900/80 border-red-400/50 animate-pulse' 
                : 'text-blue-200 bg-blue-900/80 border-blue-400/50'
            }`}>
              剩余 {displayBotCounts.RightBot}张
            </div>
            
            <div className="relative">
              {/* AI思考动态光圈 */}
              {aiProcessing && activeAiPlayer === 'RightBot' && <div className="ai-thinking-ring"></div>}
              <div 
                onClick={() => handleAiAvatarClick('RightBot')}
                onPointerDown={() => onAvatarPointerDown('RightBot')}
                onPointerUp={onAvatarPointerUp}
                onPointerLeave={onAvatarPointerUp}
    // eslint-disable-next-line react-hooks/purity
                className={`w-12 h-12 sm:w-14 sm:h-14 badge-circle badge-red flex items-center justify-center text-base sm:text-lg font-extrabold text-white bg-cover bg-center ${gameState.turn === 'RightBot' && (Date.now() - turnStartTime > 3000) ? 'cursor-pointer' : 'cursor-help'}`}
                style={{backgroundImage: `url(${SOUND_BASE}avatars/right.jpg)`, backgroundSize: 'cover', backgroundPosition: 'center'}}
              >
              </div>
            </div>
            <div className="mt-0.5 sm:mt-1 text-xs sm:text-sm font-bold text-yellow-400 drop-shadow-md bg-black/40 px-1.5 sm:px-2 rounded">
              {totalScores.RightBot}分
            </div>
          </div>
          
          {/* 出牌位（在头像左侧） */}
          <div className="flex justify-end min-w-[60px] sm:min-w-[100px] mr-6 sm:mr-6">
             <div className="flex flex-col items-end gap-1 sm:gap-2 mt-28 sm:mt-28">
               {rightHasMoves && (
                 <PlayedCardBubble moves={displayRoundMoves["RightBot"]} layout="vertical" position="right" />
               )}
             </div>
          </div>
        </div>

        {/* 小屏复盘：右家手牌贴屏幕右缘纵向多列（不挤在中间） */}
        {isReplayOpen && activeReplayPlayer === "RightBot" && displayHands?.RightBot && (
          <div className={`sm:hidden absolute right-0 ${rightHasMoves ? 'top-[10%]' : 'top-1/2'} -translate-y-1/2 z-[65] pointer-events-none`}>
            <ReplayHandStrip cards={displayHands.RightBot} variant="right" vertical compact={rightHasMoves} />
          </div>
        )}
      </div>
  );
}
