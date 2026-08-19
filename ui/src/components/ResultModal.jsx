// 3.1 拆分：从 App.jsx 抽出的展示组件（结算弹窗）
// 纯展示 + 本地 handler（调用传入的 setter/回调），不持有 App 状态。
import React, { useEffect } from 'react'
import Card from './Card'

const ResultModal = ({
  gameResult,
  gameResultVisible,
  gameId,
  lastCompletedGameId,
  setGameResultVisible,
  openReplay,
  setGameResult,
  setGameId,
  setMyHand,
  resetMoveTracking,
  closeReplay,
  setLastCompletedGameId,
  setStatusMsg,
  gameOverVoiceTimerRef,
  resultShownForGameRef,
  setSelected // Pass this to clear selection
}) => {
  useEffect(() => {
    if (gameResultVisible) {
        console.log("[ResultModal] Modal mounted/shown", { gameId, lastCompletedGameId });
    }
    return () => {
        if (gameResultVisible) console.log("[ResultModal] Modal unmounting");
    };
  }, [gameResultVisible, gameId, lastCompletedGameId]);

  if (!gameResult || !gameResultVisible) return null;
  const { scores, info, remaining_hands } = gameResult;
  const hasReplay = Boolean(lastCompletedGameId || gameId);

  const handleNextRound = (e) => {
    e.preventDefault();
    e.stopPropagation();
    console.log("[ResultModal] Next Round clicked");

    try {
        // [Optimization] Kill all pending voices/TTS immediately
        if (window.speechSynthesis) window.speechSynthesis.cancel();
        if (gameOverVoiceTimerRef.current) {
            clearTimeout(gameOverVoiceTimerRef.current);
            gameOverVoiceTimerRef.current = null;
        }

        setGameResultVisible(false);
        setGameResult(null);
        setGameId(null);
        setMyHand([]);
        setSelected([]); // Clear selection
        resetMoveTracking();
        closeReplay();
        setLastCompletedGameId(null);
        setStatusMsg("准备下一局...");
        resultShownForGameRef.current = null;
        console.log("[ResultModal] Next Round actions completed");
    } catch (err) {
        console.error("[ResultModal] Error in next round click:", err);
    }
  };

  const handleReplayClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    console.log("[ResultModal] Replay clicked");
    // [Optimization] 点击复盘立即进入，跳过结算总结语音播报（同 handleNextRound 的清理）
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      if (gameOverVoiceTimerRef.current) {
        clearTimeout(gameOverVoiceTimerRef.current);
        gameOverVoiceTimerRef.current = null;
      }
    } catch (err) {
      console.error("[ResultModal] Error killing voices on replay click:", err);
    }
    setGameResultVisible(false);
    openReplay();
  };

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/80 backdrop-blur-md p-2 sm:p-4 pointer-events-auto"
      onClick={(e) => e.stopPropagation()} // Prevent event bubbling to App
    >
      <div className="bg-slate-800 border-2 border-yellow-500 rounded-xl sm:rounded-2xl p-4 sm:p-8 max-w-5xl w-full shadow-[0_0_60px_rgba(234,179,8,0.4)] text-center max-h-[90vh] overflow-y-auto game-result-modal pointer-events-auto">
        <h2 className="text-2xl sm:text-3xl font-bold text-yellow-400 mb-1 sm:mb-2">🏆 游戏结束</h2>
        <div className="text-base sm:text-xl text-white mb-4 sm:mb-6 font-bold">{info.type}</div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-8 mb-4 sm:mb-8 text-left">
          {/* 左侧：分数结算 */}
          <div className="space-y-3 sm:space-y-4">
              <h3 className="text-lg sm:text-xl font-bold text-gray-300 border-b border-gray-600 pb-2">得分结算</h3>
              <div className="space-y-2 sm:space-y-3">
                  <div className="flex justify-between items-center bg-slate-700/50 p-3 sm:p-4 rounded-lg">
                      <span className="text-blue-300 font-bold text-sm sm:text-lg">我方 (User & Partner)</span>
                      <span className={`text-xl sm:text-2xl font-bold ${scores.User > 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {scores.User > 0 ? '+' : ''}{scores.User}
                      </span>
                  </div>
                  <div className="flex justify-between items-center bg-slate-700/50 p-3 sm:p-4 rounded-lg">
                      <span className="text-red-300 font-bold text-sm sm:text-lg">敌方 (Left & Right)</span>
                      <span className={`text-xl sm:text-2xl font-bold ${scores.LeftBot > 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {scores.LeftBot > 0 ? '+' : ''}{scores.LeftBot}
                      </span>
                  </div>
              </div>
              <div className="text-xs sm:text-sm text-gray-400 mt-3 sm:mt-4 bg-black/20 p-2 sm:p-3 rounded">
                  <div className="flex justify-between"><span>基础分:</span> <span className="text-white">{info.base}</span></div>
                  <div className="flex justify-between"><span>倍数:</span> <span className="text-white">{info.mult}</span></div>
                  {info.capped && <div className="text-yellow-500 font-bold mt-1">已封顶</div>}
              </div>
          </div>

          {/* 右侧：剩余手牌 */}
          {remaining_hands && Object.keys(remaining_hands).length > 0 && (
              <div className="space-y-3 sm:space-y-4">
                  <h3 className="text-lg sm:text-xl font-bold text-gray-300 border-b border-gray-600 pb-2">输家剩余手牌</h3>
                  <div className="space-y-2 sm:space-y-3 max-h-[300px] sm:max-h-[400px] overflow-y-auto pr-1 sm:pr-2 custom-scrollbar">
                      {Object.entries(remaining_hands).map(([player, cards]) => (
                          <div key={player} className="bg-slate-700/30 p-2 sm:p-3 rounded-lg border border-slate-600/50">
                              <div className="flex justify-between items-center mb-1 sm:mb-2">
                                  <span className="text-xs sm:text-sm font-bold text-gray-300">{player}</span>
                                  <span className="text-xs bg-slate-600 px-2 py-0.5 rounded text-gray-200">{cards.length}张</span>
                              </div>
                              <div className="flex flex-wrap gap-0.5 sm:gap-1 pl-1 sm:pl-2 pb-1 sm:pb-2">
                                  {cards.map((cid, i) => (
                                      <div key={i} className="-ml-2 sm:-ml-3 hover:ml-0 transition-all duration-200 hover:z-10 hover:scale-110">
                                          <Card id={cid} scale={0.4} />
                                      </div>
                                  ))}
                              </div>
                          </div>
                      ))}
                  </div>
              </div>
          )}
        </div>

        <div className="flex flex-row gap-3 sm:gap-4 mt-4 sm:mt-6">
          <button
            onClick={handleReplayClick}
            disabled={!hasReplay}
            className={`flex-1 py-2.5 sm:py-4 text-sm sm:text-xl font-bold rounded-lg sm:rounded-xl shadow-lg transition-all active:scale-95 ${hasReplay ? 'bg-blue-600 hover:bg-blue-500 text-white cursor-pointer' : 'bg-slate-700 text-slate-400 cursor-not-allowed'}`}
          >
            📹 复盘
          </button>
          <button
            onClick={handleNextRound}
            className="flex-1 py-2.5 sm:py-4 bg-gradient-to-r from-yellow-500 to-yellow-600 hover:from-yellow-400 hover:to-yellow-500 text-black font-bold rounded-lg sm:rounded-xl text-sm sm:text-xl shadow-lg transition-all active:scale-95 cursor-pointer"
          >
            🎮 再来一局
          </button>
        </div>
      </div>
    </div>
  );
};

export default ResultModal
