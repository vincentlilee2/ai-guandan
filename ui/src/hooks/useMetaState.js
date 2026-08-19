// 3.1/A 拆分：细粒度 hook（由 useGameState 大桶按域分组而来）。
// 机械搬运、零行为变更：原 useGameState 的声明按归属域拆分到以下 hook，App 合并调用等价。
import { useState, useRef } from 'react'
import { DEFAULT_TOTAL_SCORES } from '../lib/gameInit'

export function useMetaState() {
  const [gameId, setGameId] = useState(null);
  const [gameState, setGameState] = useState({
    state: "waiting",
    turn: "", 
    last_move: "",
    last_player: "",
    bot_cards: { RightBot: 0, PartnerBot: 0, LeftBot: 0 }
  });
  const serverSeqRef = useRef(0);
  const lastTurnRef = useRef("");
  const [_stuckTick, setStuckTick] = useState(0);
  const [aiStuck, setAiStuck] = useState(false); // AI 出牌真卡死提示标志（仅提示，不自动开新局）
  const lastStuckSeqRef = useRef(null);   // 最近一次记录的 seq，用于判定 seq 是否推进
  const stuckSinceRef = useRef(0);        // seq 开始不推进的时间戳
  const [fallbackToast, setFallbackToast] = useState(false); // AI 超时本地兜底一次性提醒
  const lastFallbackSeqRef = useRef(null); // 已提示兜底的 seq，避免轮询重复弹
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [_currentUserId, setCurrentUserId] = useState(null);
  const [userName, setUserName] = useState("");
  const [lastCompletedGameId, setLastCompletedGameId] = useState(null);
  const syncInFlightRef = useRef(false);
  const lastSyncedUserIdRef = useRef(null);
  const sessionLoggedInRef = useRef(false);
  const loginWindowRef = useRef(null);
  const validGameIdRef = useRef(null);
  const pollingTimerRef = useRef(null);
  // v2.5 权限隔离：当前有效局绑定的访问 token（开局时下发，刷新后从 sessionStorage 恢复）
  const gameTokenRef = useRef(null);

  return {
    gameId,
    setGameId,
    gameState,
    setGameState,
    serverSeqRef,
    lastTurnRef,
    _stuckTick,
    setStuckTick,
    aiStuck,
    setAiStuck,
    lastStuckSeqRef,
    stuckSinceRef,
    fallbackToast,
    setFallbackToast,
    lastFallbackSeqRef,
    isLoggedIn,
    setIsLoggedIn,
    _currentUserId,
    setCurrentUserId,
    userName,
    setUserName,
    lastCompletedGameId,
    setLastCompletedGameId,
    syncInFlightRef,
    lastSyncedUserIdRef,
    sessionLoggedInRef,
    loginWindowRef,
    validGameIdRef,
    pollingTimerRef,
    gameTokenRef,
  };
}
