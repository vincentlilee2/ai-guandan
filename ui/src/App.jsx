// game/ui/src/App.jsx
import React, { useEffect, useCallback, useMemo, useState, useRef } from 'react';
import Card from './components/Card';
import SimpleCard from './components/SimpleCard';
import { useGameStream } from './hooks/useGameStream';
import { useMetaState } from './hooks/useMetaState';
import { useHandState } from './hooks/useHandState';
import { useScoreState } from './hooks/useScoreState';
import { useTableState } from './hooks/useTableState';
import { useAudio } from './hooks/useAudio';
import PlayedCardBubble from './components/PlayedCardBubble';
import ReplayHandStrip from './components/ReplayHandStrip';
import ResultModal from './components/ResultModal';
import AuthModal from './components/AuthModal';
import CoachModal from './components/CoachModal';
import RulesModal from './components/RulesModal';
import HandCards from './components/HandCards';
import ReplayProgress from './components/ReplayProgress';
import { TableBoard } from './components/TableBoard';
import { ScorePanel } from './components/ScorePanel';
import { PLAYER_DISPLAY_NAMES } from './lib/playerNames';
import { SOUND_BASE, PLAY_SFX, BOMB_SFX, WIN_SFX, GAME_OVER_SFX, ERROR_SFX, ERROR_VOICE_BASE, SHOW_RESULT_AFTER_FLUSH_MS, MIN_RESULT_AFTER_LAST_RENDER_MS, FINISH_SETTLE_MS, createEmptyRoundMoves, PLAYER_LABELS, DEFAULT_TOTAL_SCORES, ANON_ID_KEY, LOCAL_SCORE_KEY, getAnonId, loadLocalScores, saveLocalScores, mergeScores, replaySkipTarget, replayPrevTarget } from './lib/gameInit';
import { MEMBER_LIMIT } from './lib/quota';
import { reconcileHandOrder } from './lib/handOrder';
import { fetchFeatureFlags } from './lib/config';

const API_BASE = "";
const MIN_AI_MOVE_DELAY = 800; // [Optimal] Restored from faster values
const PASS_AI_MOVE_DELAY = 450; // [Optimal] Restored from faster values
const REPLAY_STEP_INTERVAL = 1500;

function App() {
  // 检测是否为安卓系统
  const _isAndroid = useMemo(() => {
    return typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent);
  }, []);

  // 动态计算手牌显示参数（根据屏幕宽度）
  const { handDisplayParams, setHandDisplayParams, gameId, setGameId, myHand, setMyHand, handOrder, setHandOrder, selected, setSelected, passShake, setPassShake, bombTrigger, setBombTrigger, gameState, setGameState, serverSeqRef, turnStartTime, setTurnStartTime, lastTurnRef, aiStuck, setAiStuck, lastStuckSeqRef, stuckSinceRef, totalScores, setTotalScores, fallbackToast, setFallbackToast, lastFallbackSeqRef, isLoggedIn, setIsLoggedIn, _currentUserId, setCurrentUserId, userName, setUserName, gameResult, setGameResult, gameResultVisible, _setGameResultVisible, gameResultVisibleRef, lastCompletedGameId, setLastCompletedGameId, roundMoves, setRoundMoves, aiProcessing, setAiProcessing, activeAiPlayer, setActiveAiPlayer, statusMsg, setStatusMsg, isReplayOpen, setIsReplayOpen, isReplayOpenRef, replayData, setReplayData, replayIndex, setReplayIndex, replayPlaying, setReplayPlaying, replayError, setReplayError, replayLoading, setReplayLoading, processedHistoryCountRef, lastProcessedHistoryIdRef, optimisticPendingCountRef, lastMoveRenderedAtRef, finishDetectedAtRef, finishDetectedForGameRef, aiMoveQueueRef, hasReceivedHandRef, winCelebrationPlayedRef, audioUnlockedRef, audioPoolRef, winVoiceTimerRef, gameOverVoiceTimerRef, voiceQueueRef, isPlayingVoiceRef, currentAudioRef, pendingWinCelebrationRef, gameOverSummaryPlayedRef, aiQueueTimerRef, aiProcessingRef, lastAiDisplayTimeRef, replayTimerRef, resultShownForGameRef, pendingResultForGameRef, lastUserPlayTimeRef, pendingPlayCardsCountRef, cardHitRefsRef, localScoreAppliedForGameRef, syncInFlightRef, lastSyncedUserIdRef, sessionLoggedInRef, scoreFlipFace, setScoreFlipFace, loginWindowRef, _skipHistoryReplayRef, validGameIdRef, pollingTimerRef, longPressTimerRef, gameTokenRef, isActionPending, setIsActionPending } = {
    ...useMetaState(),
    ...useHandState(),
    ...useScoreState(),
    ...useTableState(),
  };

  // ---- 会员注册/登录（v3）----
  // authToken / authUser 初始化自 sessionStorage，刷新后登录态保持
  const [authToken, setAuthToken] = useState(() => {
    try { return window.sessionStorage.getItem("authToken") || null; } catch (_) { return null; }
  });
  const [authUser, setAuthUser] = useState(() => {
    try {
      const raw = window.sessionStorage.getItem("authUser");
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  });
  const [authModalOpen, setAuthModalOpen] = useState(null); // null | "login" | "account"
  const [rulesOpen, setRulesOpen] = useState(false); // 开始页「查看规则」弹窗
  const [memberQuotaToast, setMemberQuotaToast] = useState(false); // 会员达 20 局的软提醒
  const [authModalMode, setAuthModalMode] = useState("login"); // 注册/登录 tab 初始模式
  // ---- AI 教练（复盘）----
  const [coachOpen, setCoachOpen] = useState(false);
  const [coachLoading, setCoachLoading] = useState(false);
  const [coachError, setCoachError] = useState(null);
  const [coachReviews, setCoachReviews] = useState(null);
  const [coachMessage, setCoachMessage] = useState(""); // 无问题/无记录时的整局提示文案
  const [coachCached, setCoachCached] = useState(false);
  // ---- 功能开关（拉取自 /api/config，默认全关；拉取失败按关闭降级）----
  const [featureFlags, setFeatureFlags] = useState({ member_login_enabled: false, ai_coach_enabled: false });
  const memberLoginEnabled = featureFlags.member_login_enabled === true;
  const aiCoachEnabled = featureFlags.ai_coach_enabled === true;
  // fetchGameState 依赖被刻意裁剪（避免 SSE 重连），authToken 走 ref 读取防闭包过期
  const authTokenRef = useRef(authToken);
  useEffect(() => { authTokenRef.current = authToken; }, [authToken]);
  // memberLoginEnabled 同理走 ref：fetchGameState 的长回调里读最新开关值
  const memberLoginEnabledRef = useRef(memberLoginEnabled);
  useEffect(() => { memberLoginEnabledRef.current = memberLoginEnabled; }, [memberLoginEnabled]);

  // 监听窗口尺寸变化，动态调整参数（高度方向由 HandCards 内按可用高度量测缩放）
  useEffect(() => {
    const handleResize = () => {
      const width = window.innerWidth;
      let scale, overlap, paddingLeft;
      if (width <= 360) {
        scale = 0.60; overlap = '-62px'; paddingLeft = '24px';
      } else if (width <= 390) {
        scale = 0.65; overlap = '-58px'; paddingLeft = '28px';
      } else if (width <= 414) {
        scale = 0.68; overlap = '-54px'; paddingLeft = '30px';
      } else if (width <= 430) {
        scale = 0.67; overlap = '-57px'; paddingLeft = '28px';
      } else {
        scale = 0.72; overlap = '-50px'; paddingLeft = '32px';
      }
      setHandDisplayParams({ scale, overlap, paddingLeft });
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  
  // 核心状态

  // 网络抗丢包：状态序列号跟踪
  
  // [新增] 追踪当前回合开始时间，用于“催促”功能

  // 累计积分
  // 游戏结果弹窗
  const setGameResultVisible = useCallback((v) => {
    gameResultVisibleRef.current = v;
    _setGameResultVisible(v);
  }, []);

  // 前端缓存：记录本轮每个人出的牌 { User: ["一张2", "Pass"], LeftBot: ["Pass"] ... }
  // 修改为数组，支持显示历史记录

  const playErrorVoice = useCallback((errorMsg) => {
    // 播放嘟声 (已移除，避免播放错误的男声“获胜分”)
    // const beep = new Audio(ERROR_SFX);
    // beep.volume = 0.5;
    // beep.play().catch(() => {});

    // 匹配错误类型播放语音
    let voiceFile = null;
    if (errorMsg.includes("牌型不匹配")) voiceFile = "error_type_mismatch.mp3";
    else if (errorMsg.includes("点数不够大")) voiceFile = "error_rank_too_small.mp3";
    else if (errorMsg.includes("张数不一致")) voiceFile = "error_count_mismatch.mp3";
    else if (errorMsg.includes("炸弹太小")) voiceFile = "error_bomb_too_small.mp3";
    else if (errorMsg.includes("管不上炸弹")) voiceFile = "error_bomb_vs_rocket.mp3"; // 普通管不上炸弹
    else if (errorMsg.includes("无效的牌型")) voiceFile = "error_invalid_type.mp3";
    else if (errorMsg.includes("没轮到你")) voiceFile = "error_not_your_turn.mp3";
    else if (errorMsg.includes("同花顺")) voiceFile = "error_straight_flush_rank.mp3";

    if (voiceFile) {
      // 立即播放语音，不再等待
      const voice = new Audio(`${ERROR_VOICE_BASE}${voiceFile}`);
      voice.volume = 0.8;
      voice.play().catch(() => {});
    }
  }, []);



  useEffect(() => { isReplayOpenRef.current = isReplayOpen; }, [isReplayOpen]);


  // [新增] 标记本局游戏是否已经收到过手牌（防止开局空手牌误判胜利）
  // [新增] 用于防重复播放庆祝音效

  // 以下 audio 相关 ref 由 useAudio hook 共享使用（playSound 语音队列 / ResultModal 也会用到，故留在组件内）
  const { unlockAudio, speakWithGirlVoice, playWinCelebration, playGameOverSummary, playExplosionSound } =
    useAudio({ audioUnlockedRef, audioPoolRef, winCelebrationPlayedRef, winVoiceTimerRef, gameOverVoiceTimerRef });

  // [新增] 延迟播放庆祝音效：等待出牌队列中该玩家的牌出完
  // [新增] 用于防重复播放结算音效

  // 持久化 gameId 到 sessionStorage，支持手动刷新后恢复牌局
  useEffect(() => {
    if (gameId) {
      sessionStorage.setItem('restoreGameId', gameId);
    }
  }, [gameId]);

  // 页面刷新恢复：自动重新加入之前卡住的牌局
  useEffect(() => {
    const restoredId = sessionStorage.getItem('restoreGameId');
    if (restoredId && !gameId) {
      // v2.5：一并恢复该局绑定的访问 token（无则置空——老局兼容后端放行）
      const restoredToken = sessionStorage.getItem('restoreGameToken') || '';
      sessionStorage.removeItem('restoreGameId');
      sessionStorage.removeItem('restoreGameToken');
      // 仅需清除标记，值本身未使用
      sessionStorage.removeItem('restoreHistoryHid');
      (async () => {
        try {
          const res = await fetch(`/api/${restoredId}/state?token=${encodeURIComponent(restoredToken)}`);
          if (res.ok) {
            const data = await res.json();
            if (data.state === 'playing' || data.state === 'finished') {
              console.log('[Restore] 恢复牌局', restoredId, 'seq:', data.seq, 'turn:', data.turn);
              setGameId(restoredId);
              validGameIdRef.current = restoredId;
              gameTokenRef.current = restoredToken;
              setMyHand(data.my_hand || []);
              setHandOrder(data.my_hand || []);

              // 恢复最近3轮的出牌显示（从 history_len 往前取）
              const historyLen = data.history_len || 0;
              const restoreFrom = Math.max(0, historyLen - 12); // 最多显示最近3轮(每轮最多4人)
              lastProcessedHistoryIdRef.current = restoreFrom;
              serverSeqRef.current = data.seq || 0;
              setGameState({
                state: data.state,
                turn: data.turn,
                last_move: data.last_move,
                last_player: data.last_player,
                bot_cards: data.bot_cards_count || { RightBot: 27, PartnerBot: 27, LeftBot: 27 }
              });
              hasReceivedHandRef.current = true;
              setStatusMsg('已恢复牌局');

              // 如果当前是AI的回合 → 强制重试prompt
              if (data.state === 'playing' && data.turn !== 'User') {
                console.log('[Restore] 轮到', data.turn, '，触发AI强制重试');
                setTimeout(async () => {
                  try {
                    await fetch(`/api/ai_retry`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ game_id: restoredId, force: true, token: restoredToken })
                    });
                  } catch (e) { console.error('[Restore] AI重试失败', e); }
                }, 500);
              }
            } else {
              console.log('[Restore] 牌局已失效');
              setStatusMsg('牌局已结束，请开启新牌局');
            }
          } else {
            console.log('[Restore] 牌局已过期，自动开启新牌局');
            setTimeout(() => { startGame(); }, 300);
          }
        } catch (e) {
          console.error('[Restore] 恢复失败，自动开启新牌局', e);
          setTimeout(() => { startGame(); }, 300);
        }
      })();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- 故意留空：恢复逻辑只在挂载时跑一次，gameId 不可加入否则每次换局会重置状态

  // [修改] AI 出牌卡死精确判定：Bot 回合且 seq 连续 60s 无推进 = 真卡死 → 仅弹提示，不自动开新局
  // 依据 seq 是否推进（而非 turn 是否变化），避免正常局被误杀；只有真卡死才提示用户手动重开
  // 换局/换回合时重置计时起点（依赖 gameId，确保点“重开新局”后旧卡死状态被彻底清除）
  useEffect(() => {
    // 一局已结束：seq 不再推进是正常现象（结算数据已完整），不再判定卡死。
    // 否则在「弹窗显示前低速轮询重试」的窗口期，会误弹「AI 出牌似乎卡住了」。
    if (gameState.state === "finished") {
      setAiStuck(false);
      return;
    }
    let timer = null;
    // 新评估起点：每次 effect 重跑（换局/换回合）都重置卡死计时
    lastStuckSeqRef.current = null;
    stuckSinceRef.current = Date.now();
    if (gameState.turn && gameState.turn !== 'User') {
      timer = setInterval(() => {
        const seqNow = serverSeqRef.current;
        if (lastStuckSeqRef.current !== null && seqNow !== lastStuckSeqRef.current) {
          // seq 在推进：正常，重置计时并清除卡死提示
          lastStuckSeqRef.current = seqNow;
          stuckSinceRef.current = Date.now();
          setAiStuck(prev => (prev ? false : prev));
        } else if (lastStuckSeqRef.current === null) {
          lastStuckSeqRef.current = seqNow;
          stuckSinceRef.current = Date.now();
        } else if (Date.now() - stuckSinceRef.current >= 90000 && !isActionPending) {
          // 90s 无推进且非玩家操作中 → 判定真卡死，弹提示（不自动开新局）
          // 90s 落后于后端 60s 本地兜底硬时限：兜底出牌会推进 seq，计时被自动重置，弹窗只在真正死锁时出现
          setAiStuck(prev => (prev ? prev : true));
        }
      }, 1000);
    } else {
      // 轮到玩家或无人回合：不判定卡死
      setAiStuck(false);
    }
    return () => { if (timer) clearInterval(timer); };
  }, [gameState.turn, gameState.state, isActionPending, gameId]);

  const handleAiAvatarClick = useCallback(async (clickedPlayer) => {
    // 只有轮到该玩家 且 思考时间超过 3 秒才生效
    const isThisBotTurn = gameState.turn === clickedPlayer;
    const thinkingTime = Date.now() - turnStartTime;
    const isStuck = thinkingTime > 3000;

    if (isThisBotTurn && isStuck) {
        // [Add Voice Feedback]
        // 播放该 AI 对应的“等我想想”语音文件，而不是通用的 TTS
        const voiceFile = `${SOUND_BASE}sounds/${clickedPlayer}/wait_thinking.mp3`;
        const audio = new Audio(voiceFile);
        audio.play().catch(err => {
            console.warn("Play wait_thinking.mp3 failed, fallback to TTS:", err);
            speakWithGirlVoice("稍等，让我想想...");
        });
        
        // 触发后端重试
        const thinkingSec = Math.round(thinkingTime / 1000);
        const useForce = thinkingTime > 10000; // 超过10秒，实际重发prompt
        try {
            await fetch(`/api/ai_retry`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ game_id: gameId, force: useForce, token: gameTokenRef.current })
            });
            console.log(`[UI] ${useForce ? '强制' : '软'}重试 ${clickedPlayer} (${thinkingSec}秒)`);
            if (useForce) setStatusMsg(`${clickedPlayer} 正在重新思考...`);
        } catch (e) {
            console.error("Retry trigger failed", e);
        }
    }
  }, [gameState.turn, turnStartTime, gameId, speakWithGirlVoice]);

  // [新增] 修复无效轮询的Ref

  const handleAiAvatarLongPress = useCallback(async (player) => {
    // 震动反馈 (如果设备支持)
    if (navigator.vibrate) navigator.vibrate(50);
    
    const ok = window.confirm(`我刚才出牌错误了吗？`);
    if (ok) {
        try {
            // 复盘场景：取当前复盘指针所在这手的 prompt（精确对上用户看到的那一手）
            let payload = { player_name: player, game_id: gameId, token: gameTokenRef.current };
            if (isReplayOpen && replayData && Array.isArray(replayData.history)) {
                const move = replayData.history[replayIndex];
                if (move && (move.user_prompt || move.ai_response || move.system_prompt)) {
                    payload.system_prompt = move.system_prompt || "";
                    payload.user_prompt = move.user_prompt || "";
                    payload.ai_response = move.ai_response || "";
                }
                // 若无 prompt 字段（旧历史文件），仍带 game_id，后端按局缓存取；取不到回退 no_context
            }
            const res = await fetch(`/api/report_error`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status === 'success') {
                speakWithGirlVoice("收到，我会反思这次错误。");
                console.log(`[UI] 已记录 ${player} 的错误到后端。`, payload.user_prompt ? "(复盘当前手prompt)" : "(本局最近一次prompt)");
            } else if (data.status === 'no_context') {
                speakWithGirlVoice("还没找到我的本局出牌记录提示词。");
            }
        } catch (e) {
            console.error("Report error failed", e);
            speakWithGirlVoice("保存记录失败。");
        }
    }
  }, [gameId, isReplayOpen, replayData, replayIndex, speakWithGirlVoice]);

  const onAvatarPointerDown = useCallback((player) => {
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = setTimeout(() => {
        handleAiAvatarLongPress(player);
        longPressTimerRef.current = null;
    }, 3000);
  }, [handleAiAvatarLongPress]);

  const onAvatarPointerUp = useCallback(() => {
    if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current);
        longPressTimerRef.current = null;
    }
  }, []);

  const setCardHitRef = useCallback((key, node) => {
    if (node) {
      cardHitRefsRef.current.set(key, node);
    } else {
      cardHitRefsRef.current.delete(key);
    }
  }, []);


  // 自动触发：有玩家完牌胜出时，播放庆祝音效和语音
  useEffect(() => {
    // 检查是否有玩家手牌为0（即胜出）
    // 只在非结算、非回放时触发
    if (!gameResultVisible && !isReplayOpen) {
      if (!gameId) {
          // 新游戏重置
          winCelebrationPlayedRef.current.clear();
          hasReceivedHandRef.current = false;
          return;
      }
      
      // 更新已收到手牌标记
      if (myHand.length > 0) {
        hasReceivedHandRef.current = true;
      }

      const players = ["User", "LeftBot", "RightBot", "PartnerBot"];
      
      players.forEach(player => {
        // 如果已经庆祝过，跳过
        if (winCelebrationPlayedRef.current.has(player)) return;

        // 检查该玩家手牌是否为0
        let isWinner = false;
        if (player === 'User') {
            // 注意：myHand 初始化为空数组，开局时可能也为空，需要结合 gameState.state === 'playing'
            // 必须确认为已发过牌（hasReceivedHandRef），否则刚开局的瞬间空数组会导致误判
            isWinner = (myHand.length === 0 && gameState.state === 'playing' && hasReceivedHandRef.current);
        } else {
            // 电脑玩家：必须在游戏中，且手牌数为0
            isWinner = (gameState.state === 'playing' && gameState.bot_cards?.[player] === 0);
        }

        // 只要手牌是0且是playing，就是赢了（不管是刚出完还是出完很久）
        if (isWinner) {
           console.log(`检测到 ${player} 胜出，触发庆祝逻辑`);
           winCelebrationPlayedRef.current.add(player);
           
           // [Mod] 区分直接触发还是等待队列动画
           // 如果该玩家在 AI 动画队列中还有未播放的动作，说明“最后一手牌”还没展示出来。
           // 此时标记 pending，交给 showMove 去触发。
           const hasPendingMoves = aiMoveQueueRef.current.some(item => item.player === player);
           
           if (hasPendingMoves) {
               console.log(`- ${player} 尚有动画在队列中，标记 pending`);
               pendingWinCelebrationRef.current.add(player);
           } else {
               // 队列无动作（例如我是 User 直接出牌，或者队列已播完），直接延时触发
               // 这里的 1200ms 是等待界面上“手牌飞出 -> 落地”的动画视觉稳定
               console.log(`- ${player} 无Pending动画，直接延时触发`);
               setTimeout(() => {
                   playWinCelebration(player);
               }, 1200);
           }
        }
      });
    } else if (!gameId) {
       winCelebrationPlayedRef.current.clear();
       hasReceivedHandRef.current = false;
    }
  }, [myHand, gameState.bot_cards, gameState.state, gameResultVisible, isReplayOpen, playWinCelebration, gameId]);

  // 自动触发：结算弹窗弹出时，自动播报结算语音
  useEffect(() => {
    if (gameResultVisible && gameResult) {
      if (!gameOverSummaryPlayedRef.current) {
        gameOverSummaryPlayedRef.current = true;
        playGameOverSummary(gameResult);
      }
    } else {
      // 弹窗关闭，重置
      gameOverSummaryPlayedRef.current = false;
    }
  }, [gameResultVisible, gameResult, playGameOverSummary]);

  // 自动清理出错提示（⚠️ 前缀），避免长时间遮挡中间状态。
  // 之前只清理旧格式「⚠️ 出牌失败」，新加的「⚠️ 出牌失败：…」「⚠️ 出牌结果未确认…」
  // 「⚠️ PASS失败：…」等前缀未被匹配，会永久留在桌面中央。统一按 ⚠️ 前缀清理。
  useEffect(() => {
    if (typeof statusMsg === "string" && statusMsg.startsWith("⚠️")) {
      const timer = setTimeout(() => {
        setStatusMsg(prev => (prev.startsWith("⚠️") ? "" : prev));
      }, 3500);
      return () => clearTimeout(timer);
    }
  }, [statusMsg]);

  // 新增：全局动作锁，防止按钮连击导致网络风暴或状态错乱

  const parseApiError = useCallback(async (res) => {
    try {
      const text = await res.text();
      if (!text) return null;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed.detail === 'string' && parsed.detail.trim()) {
          return parsed.detail.trim();
        }
      } catch (_) {
        // ignore JSON parse
      }
      return text.trim() || null;
    } catch (_) {
      return null;
    }
  }, []);

  const applyLocalScoreDelta = useCallback((deltaScores) => {
    const current = loadLocalScores();
    const next = mergeScores(current, deltaScores);
    saveLocalScores(next);
    setTotalScores(next);
  }, []);

  const clearLocalScores = useCallback(() => {
    saveLocalScores({ ...DEFAULT_TOTAL_SCORES });
  }, []);

  const syncLocalScores = useCallback(async (userId) => {
    if (!userId || syncInFlightRef.current) return;
    const localScores = loadLocalScores();
    const hasLocal = Object.values(localScores).some(v => Number(v) !== 0);
    if (!hasLocal) return;
    syncInFlightRef.current = true;
    try {
      const res = await fetch(`/api/score/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ anon_id: getAnonId(), local_scores: localScores })
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data?.total_scores) {
        setTotalScores(data.total_scores);
      }
      setIsLoggedIn(true);
      setCurrentUserId(userId);
      clearLocalScores();
    } catch (_) {
      // ignore
    } finally {
      syncInFlightRef.current = false;
    }
  }, [clearLocalScores]);

  // ---- 会员认证 API（v3）----
  const authHeaders = useCallback((token) => ({
    "Content-Type": "application/json",
    ...(token ? { "Authorization": `Bearer ${token}` } : {}),
  }), []);

  const applyAuthSession = useCallback((token, user) => {
    setAuthToken(token);
    setAuthUser(user);
    try {
      window.sessionStorage.setItem("authToken", token);
      window.sessionStorage.setItem("authUser", JSON.stringify({ nickname: user.nickname, email: user.email }));
    } catch (_) { /* ignore */ }
    setIsLoggedIn(true);
    setUserName(user.nickname || "");
  }, [setIsLoggedIn, setUserName]);

  const clearAuthSession = useCallback(() => {
    setAuthToken(null);
    setAuthUser(null);
    try { window.sessionStorage.removeItem("authToken"); window.sessionStorage.removeItem("authUser"); } catch (_) { /* ignore */ }
    setIsLoggedIn(false);
    setUserName("");
  }, [setIsLoggedIn, setUserName]);

  // AuthModal 内直接用 fetch 调 /api/auth/*，这里只需 me（校验/刷新局数）与 logout
  const fetchMe = useCallback(async (token) => {
    const res = await fetch(`/api/auth/me`, { headers: authHeaders(token) });
    if (!res.ok) return null;
    return res.json().catch(() => null);
  }, [authHeaders]);

  const logoutUser = useCallback(async () => {
    try { await fetch(`/api/auth/logout`, { method: "POST", headers: authHeaders(authToken) }); } catch (_) { /* ignore */ }
    clearAuthSession();
  }, [authHeaders, authToken, clearAuthSession]);

  const uploadGameScore = useCallback(async (deltaScores) => {
    try {
      const res = await fetch(`/api/score/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ anon_id: getAnonId(), local_scores: deltaScores })
      });
      if (res.ok) {
        const data = await res.json();
        if (data?.total_scores) {
          setTotalScores(data.total_scores);
          return true; // 上传成功
        }
      }
      return false; // 响应不OK
    } catch (e) {
      console.error("Failed to upload game score", e);
      return false; // 网络错误等
    }
  }, []);

  const refreshScoreState = useCallback(async () => {
    // v3 会员会话校验：本地有 authToken → 调 /api/auth/me 验证，
    // 有效则恢复登录态，无效则清空（token 过期/服务端注销）。
    // 会员登录开关关闭时跳过校验（无账号体系），残留 token 视为游客。
    const localToken = memberLoginEnabled ? authTokenRef.current : null;
    if (localToken) {
      const info = await fetchMe(localToken);
      if (info) {
        setAuthUser({ nickname: info.nickname, email: info.email, plays_today: info.plays_today, limit: info.limit });
        setIsLoggedIn(true);
        setUserName(info.nickname || "");
        try { window.sessionStorage.setItem("authUser", JSON.stringify({ nickname: info.nickname, email: info.email })); } catch (_) { /* ignore */ }
        // 服务器权威得分（官网/本地 store 累计），仅当有效时覆盖本地兜底
        if (info.total_scores) setTotalScores(info.total_scores);
      } else {
        clearAuthSession();
      }
    }

    try {
      const sessionRes = await fetch(`/session`, { credentials: "include" });
      if (sessionRes.ok) {
        const sessionData = await sessionRes.json();
        const sessionLoggedIn = sessionData?.errno === "0" && sessionData?.data?.username;
        if (sessionLoggedIn) {
          sessionLoggedInRef.current = true;
          setIsLoggedIn(true);
          setUserName(sessionData.data.username || "");
        } else {
          sessionLoggedInRef.current = false;
          // v3：存在有效会员 auth 会话时不被旧网关 /session 的 errno!=0 清空用户名
          if (!authTokenRef.current) setUserName("");
        }
      }
    } catch (_) {
      sessionLoggedInRef.current = false;
      // v3：同上，网络异常时保留会员会话的用户名
      if (!authTokenRef.current) setUserName("");
    }

    try {
      const res = await fetch(`/api/score`, { credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      const loggedIn = Boolean(data?.logged_in && Number.isFinite(data?.user_id));
      if (loggedIn) {
        setIsLoggedIn(true);
        setCurrentUserId(data.user_id);
        if (data?.total_scores) {
          setTotalScores(prev => {
            const isSame = JSON.stringify(prev) === JSON.stringify(data.total_scores);
            return isSame ? prev : data.total_scores;
          });
        }
        if (lastSyncedUserIdRef.current !== data.user_id) {
          lastSyncedUserIdRef.current = data.user_id;
          syncLocalScores(data.user_id);
        }
      } else if (!sessionLoggedInRef.current && !authTokenRef.current) {
        // v3：存在有效会员 auth 会话时不被 /api/score 重置（该接口对会员登录态不感知）
        setIsLoggedIn(false);
        setCurrentUserId(null);
        lastSyncedUserIdRef.current = null;
        setTotalScores(loadLocalScores());
      }
    } catch (_) {
      // ignore
    }
  }, [syncLocalScores, fetchMe, clearAuthSession, memberLoginEnabled]);

  useEffect(() => {
    setTotalScores(loadLocalScores());
    refreshScoreState();
    // 拉取功能开关（会员登录 / AI 教练）；失败时保持默认关闭
    fetchFeatureFlags().then((flags) => setFeatureFlags(flags));
  }, [refreshScoreState]);

  useEffect(() => {
    let loginTimer = null;
    let backTimer = null;

    const schedule = () => {
      loginTimer = setTimeout(() => {
        setScoreFlipFace("login");
        backTimer = setTimeout(() => {
          setScoreFlipFace("score");
          schedule();
        }, 5000);
      }, isLoggedIn ? 5000 : 10000);
    };

    schedule();
    return () => {
      if (loginTimer) clearTimeout(loginTimer);
      if (backTimer) clearTimeout(backTimer);
    };
  }, [isLoggedIn]);

  const handleScoreCardClick = useCallback(() => {
    // 会员登录未开通：点击无入口，静默返回（分数卡仍显示得分）
    if (!memberLoginEnabled) return;
    if (isLoggedIn || authUser) {
      setAuthModalOpen("account");
      return;
    }
    setAuthModalOpen("login");
  }, [isLoggedIn, authUser, memberLoginEnabled]);

  useEffect(() => {
    const handler = (event) => {
      if (!event || !event.data) return;
      if (event.data.type === "GAME_RETURN") {
        if (loginWindowRef.current && !loginWindowRef.current.closed) {
          loginWindowRef.current.close();
        }
        
        console.log("Returned from iframe/window, refreshing state...");
        // 强行刷新状态：1. 重新检查 session; 2. 重新拉取分数
        refreshScoreState().then(() => {
             // 额外检查：如果现在是登录状态，强制再拉一次分数确保准确
             if (sessionLoggedInRef.current || isLoggedIn) {
                 fetch(`/api/score`, { credentials: "include" })
                    .then(r => r.json())
                    .then(d => {
                        if (d.total_scores) {
                            setTotalScores(d.total_scores);
                        }
                    })
                    .catch(()=>{});
             } else {
                // 如果未登录，重新加载本地
                setTotalScores(loadLocalScores());
             }
        });
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [refreshScoreState, isLoggedIn]);

  const _handleLogout = useCallback(() => {
      // 1. 清理 Session 状态
      fetch('/logout', { method: "GET" }).then(() => {
          // 2. 清理前端状态
          setIsLoggedIn(false);
          setCurrentUserId(null);
          setUserName("");
          lastSyncedUserIdRef.current = null;
          
          // 3. 按照需求：登出后从零开始记录
          // 清空本地存储的旧记录
          clearLocalScores(); 
          setTotalScores({ ...DEFAULT_TOTAL_SCORES });
          
          // 4. 重置翻转卡片提示
          setScoreFlipFace("score");
      });
  }, [clearLocalScores]);

  const removePlayedFromHand = useCallback((playedIds) => {
    if (!Array.isArray(playedIds) || playedIds.length === 0) return;
    // 复盘模式下 hand 由 replayHands 推导，不在此处修改
    if (isReplayOpen) return;
    const removal = new Set(playedIds);
    setMyHand(prev => (Array.isArray(prev) ? prev.filter(id => !removal.has(id)) : prev));
    // v3.2：用户排列同步剔除已出牌（即使两次 poll 之间也保持正确）
    setHandOrder(prev => (Array.isArray(prev) ? prev.filter(id => !removal.has(id)) : prev));
  }, [isReplayOpen]);

  // v3.2：拖拽组排 —— 从 from 移到 to（扁平索引），to 为插入位置
  const handleReorder = useCallback((from, to) => {
    if (isReplayOpen) return;
    setHandOrder(prev => {
      if (!Array.isArray(prev) || from < 0 || from >= prev.length || to < 0 || to > prev.length) return prev;
      if (to === from || to === from + 1) return prev; // 无操作
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }, [isReplayOpen]);

  const clearAiTimer = useCallback(() => {
    if (aiQueueTimerRef.current) {
      clearTimeout(aiQueueTimerRef.current);
      aiQueueTimerRef.current = null;
    }
  }, []);

  const makeHistoryKey = useCallback((entry) => {
    if (!entry) return '';
    const { player = '', action = '', desc = '', cards = [] } = entry;
    // 强制排序 card IDs，确保乐观更新（User选牌顺序）与后端返回（通常有序）生成的 Key 一致
    // 从而避免 Key 不匹配导致的重复播报/显示
    const sorted = cards ? [...cards].sort() : [];
    const cardsKey = sorted.join(',');
    if (player === 'User') {
      return `${player}|${action}|${cardsKey}`;
    }
    return `${player}|${action}|${desc || ''}|${cardsKey}`;
  }, []);

  const appendMove = useCallback((player, rawMove) => {
    if (!player || !rawMove) return;
    const move = {
      action: rawMove.action,
      desc: rawMove.desc ?? (rawMove.action === 'PASS' ? 'PASS' : rawMove.action || ''),
      cards: rawMove.cards ? [...rawMove.cards] : [],
      ts: Date.now()
    };

    setRoundMoves(prev => {
      const next = { ...prev };
      const prevList = next[player] ? [...next[player]] : [];
      next[player] = [move, ...prevList].slice(0, 12);
      return next;
    });

    lastMoveRenderedAtRef.current = Date.now();

    if (player === 'User') {
      lastAiDisplayTimeRef.current = Date.now();
    }
  }, [setRoundMoves]);

  // 处理语音队列播放
  const processVoiceQueue = useCallback(() => {
    if (isPlayingVoiceRef.current || voiceQueueRef.current.length === 0) {
      return;
    }

    // [New] Prune queue if backlog is too large to ensure low latency
    // [优化] 阈值收紧(>2 → >1)：AI 一口气出到轮到 User 时，若已有 1 条语音在播，
    // 后续新语音直接丢弃，避免「出牌状态要等上家语音播完」的观感延迟。
    if (voiceQueueRef.current.length > 1) {
       // Filter out PASS moves to catch up
       const originalLen = voiceQueueRef.current.length;
       voiceQueueRef.current = voiceQueueRef.current.filter(item => !item.isPass);

       // If still too long, only keep the latest 2 items
       if (voiceQueueRef.current.length > 2) {
           voiceQueueRef.current = voiceQueueRef.current.slice(-2);
       }
       if (originalLen !== voiceQueueRef.current.length) {
          console.log(`[Audio] Pruned queue from ${originalLen} to ${voiceQueueRef.current.length} to reduce lag`);
       }
    }
    
    // [Check again after pruning]
    if (voiceQueueRef.current.length === 0) return;

    isPlayingVoiceRef.current = true;
    const { player, filename, audioPath, fullText, isPass } = voiceQueueRef.current.shift();

    // 播放出牌音效
    try {
      // 调试：打印文件名以便排查同花顺特效
      console.log(`[Animation] filename: ${filename}, fullText: ${fullText}`);
      const isBomb = filename === 'bomb' || filename === 'flush';
      if (isBomb) {
        setBombTrigger(Date.now());
        playExplosionSound(); // 使用合成的爆炸音效
      } else {
        // 恢复出牌音效 (Restored)
        // 使用独立的对象池 key 'play_sfx' 避免与 global_voice_player 冲突
        const sfxKey = 'play_sfx';
        let sfx = audioPoolRef.current.get(sfxKey);
        if (!sfx) {
          sfx = new Audio(PLAY_SFX);
          audioPoolRef.current.set(sfxKey, sfx);
        }
        // 简单的重置逻辑，不做复杂判断，确保快速响应
        sfx.volume = isPass ? 0.3 : 0.6; 
        sfx.currentTime = 0;
        
        // Fire and forget, 就算失败也不要阻塞后续语音
        sfx.play().catch(e => {
            // 忽略 AbortError (可能是上一个 sfx 还没播完就被切了)
            if (e.name !== 'AbortError') {
                console.warn("SFX Play Error:", e);
            }
        });
      }
    } catch (err) {
      console.log('SFX block error:', err);
    }


    // 播放语音文件
    // [Mod] Strict Single-Player Mode for Voice to avoid resource conflicts
    let audio = audioPoolRef.current.get('global_voice_player');
    if (!audio) {
      audio = new Audio();
      audioPoolRef.current.set('global_voice_player', audio);
    }
    
    // Always call load() when src changes to ensure fresh state
    const normalizedPath = audioPath.startsWith('/') ? audioPath : `/${audioPath}`;
    if (audio.src !== window.location.origin + normalizedPath) {
        audio.src = normalizedPath;
        audio.load();
    }
    
    currentAudioRef.current = audio;
    audio.volume = 1.0; 
    
    // [Feature] Catch up if queue is backed up
    if (voiceQueueRef.current.length >= 2) { 
       audio.playbackRate = 1.6; 
    } else {
       audio.playbackRate = 1.0;
    }
    
    const onAudioEnd = () => {
      audio.onended = null;
      audio.onerror = null;
      currentAudioRef.current = null;
      isPlayingVoiceRef.current = false;
      processVoiceQueue();
    };

    const onAudioError = (err) => {
      // If aborted, don't fallback to TTS, just move to next
      if (err && err.name === 'AbortError') {
         onAudioEnd();
         return;
      }
      
      console.log(`[Audio Error] Path: ${audioPath}`, err);
      audio.onended = null;
      audio.onerror = null;

      if ('speechSynthesis' in window) {
        // 先检查是否已有正在说的话，如果有且是重要播报（如胜利），可能需要权衡。
        // 但这里是 move 语音，通常优先级较低，可以被覆盖。
        
        // 构造 TTS
        const u = new SpeechSynthesisUtterance(fullText);
        u.lang = 'zh-CN';
        u.volume = 1;
        
        // 根据角色设置不同音色风格
        if (player === 'RightBot') { u.pitch = 1; u.rate = 1.1; }
        else if (player === 'PartnerBot') { u.pitch = 1.3; u.rate = 1.1; }
        else if (player === 'LeftBot') { u.pitch = 0.9; u.rate = 1.0; }
        
        // 绑定结束回调，继续队列
        u.onend = onAudioEnd;
        u.onerror = onAudioEnd;
        
        window.speechSynthesis.speak(u);
      } else {
        onAudioEnd();
      }
    };

    audio.onended = onAudioEnd;
    audio.onerror = onAudioError;
    
    // [Mod] Simplified playback logic. 
    // Removed complex timeout/error handling that might prematurely abort local playback.
    
    const playPromise = audio.play();
    if (playPromise !== undefined) {
        playPromise
          .then(() => {
             // Started successfully
          })
          .catch((err) => {
             // Only log real errors, ignore standard "interrupted" errors since we are doing interrupt-on-purpose now
             if (err.name !== 'AbortError') {
                 console.log(`Audio Play Error for ${player}: ${err.name} - ${err.message}`);
                 onAudioError(err);
             } else {
                 // If aborted, it means we probably started playing the NEXT sound, so just cleanup
                 // Don't call onAudioError which would trigger TTS
             }
            });
          }
    }

  , [playExplosionSound]);

  const resolveVoiceFilename = useCallback((player, moveOrDesc) => {
    let desc = "";
    let action = "";
    if (typeof moveOrDesc === 'string') {
        desc = moveOrDesc;
    } else if (moveOrDesc) {
        desc = moveOrDesc.desc || "";
        action = moveOrDesc.action || "";
    }

    desc = desc.replace(/[♣♠♦♥]/g, "");
    let filename = "pass";
    let rank = "";

    if (action === "PASS" || desc.toUpperCase().includes("PASS") || desc.includes("不出")) {
      filename = "pass";
    } else if (desc.startsWith("一张")) {
      rank = desc.replace("一张", "").trim().toUpperCase();
      filename = `single_${rank}`;
    } else if (desc.startsWith("对")) {
      rank = desc.replace("对", "").trim().toUpperCase();
      filename = `pair_${rank}`;
    } else if (desc.startsWith("三张")) {
      rank = desc.replace("三张", "").trim().toUpperCase();
      filename = `triple_${rank}`;
    } else if (desc.includes("带对") || (desc.includes("三") && desc.includes("带"))) {
      const match = desc.match(/三(?:张)?([0-9JQKA2jqka2小大]+)/i);
      if (match) {
        rank = match[1].toUpperCase();
        filename = `sandaier_${rank}`; 
      } else {
        filename = "triple_pair";
      }
    } else if (desc.includes("顺子")) {
      filename = "straight";
    } else if (desc.includes("连对")) {
      filename = "pairs_straight";
    } else if (desc.includes("钢板")) {
      filename = "plate";
    } else if (desc.includes("炸弹") || /\d+张/.test(desc)) {
      filename = "bomb";
    } else if (desc.includes("同花顺")) {
      filename = "flush";
    } else if (desc.includes("天王炸") || desc.includes("四大天王") || desc.includes("天王")) {
      filename = "bomb"; // 将天王炸也映射为炸弹
    } else {
      filename = "play"; 
    }
    
    filename = filename.replace("小王", "joker_s").replace("大王", "joker_b");
    return filename;
  }, []);

  const playAiVoice = useCallback(async (player, moveOrDesc) => {
    if (!player) return;
    
    // 确保音频上下文已解锁
    if (!audioUnlockedRef.current) unlockAudio();

    const filename = resolveVoiceFilename(player, moveOrDesc);
    const isPass = filename === "pass";

    let desc = typeof moveOrDesc === 'string' ? moveOrDesc : (moveOrDesc?.desc || "");

    // 3. 确定身份称呼
    let identity = "";
    if (player === "RightBot") identity = "下家";
    else if (player === "PartnerBot") identity = "对家";
    else if (player === "LeftBot") identity = "上家";

    let fullText = desc;
    if (identity) {
      if (filename === "pass") {
        fullText = `${identity}过牌`;
      } else {
        fullText = `${identity}出牌，${desc}`;
      }
    }

    // 4. 添加到语音队列
    // Ensure absolute path by starting with / if SOUND_BASE is missing it
    const base = SOUND_BASE.startsWith('/') ? SOUND_BASE : `/${SOUND_BASE}`;
    const audioPath = `${base}sounds/${player}/${filename}.mp3`;
    
    // [Optimization] Immediate Interrupt Logic
    // [优化] 关键：只打断「当前正在播的」语音以让新语音立即发声；若语音队列还有积压
    // （AI 已轮到 User、或连出多手），新语音不再打断，而是把队列清掉直接插队播放当前
    // 这手——避免「上家语音一条条播、出牌状态迟迟不显示」的观感延迟。
    if (voiceQueueRef.current.length === 0 && currentAudioRef.current && !currentAudioRef.current.paused) {
        try {
            currentAudioRef.current.pause();
            currentAudioRef.current.currentTime = 0;
        } catch (_err) {
            // 音频已被浏览器回收或未开始播放，忽略
        }
    }

    voiceQueueRef.current = [];
    isPlayingVoiceRef.current = false;
    const preloadedAudio = null;

    voiceQueueRef.current.push({
      player,
      filename,
      audioPath,
      fullText,
      isPass,
      preloadedAudio 
    });
 
    processVoiceQueue();
  }, [unlockAudio, processVoiceQueue, resolveVoiceFilename]);

  const processAiQueue = useCallback(() => {
    if (aiMoveQueueRef.current.length === 0) {
      aiProcessingRef.current = false;
      setAiProcessing(false);
      setActiveAiPlayer("");
      return;
    }

    aiProcessingRef.current = true;
    setAiProcessing(true);

    const { player, move } = aiMoveQueueRef.current[0];
    setActiveAiPlayer(player || "");

    const now = Date.now();
    const elapsed = now - lastAiDisplayTimeRef.current;
    
    // [Mod] Use faster delay for PASS moves
    const isPass = !move || !move.cards || move.cards.length === 0 || move.action === "PASS";
    const targetDelay = isPass ? PASS_AI_MOVE_DELAY : MIN_AI_MOVE_DELAY;
    let wait = Math.max(0, targetDelay - elapsed);

    // [Mod] If there's a backlog in AI moves, cut the wait time but keep it visible 
    const backlog = aiMoveQueueRef.current.length;
    if (backlog > 2) {
        wait = Math.min(wait, 350);
    } else if (backlog > 1) {
        wait = Math.min(wait, 500);
    }
    
    // [Mod] Shorten the wait slightly if voice queue is empty to make it snappy
    const voiceBacklog = voiceQueueRef.current.length;
    if (voiceBacklog === 0 && wait > 100) {
        wait = Math.max(100, wait - 100);
    }
    
    const showMove = () => {
      const current = aiMoveQueueRef.current.shift();
      aiQueueTimerRef.current = null;
      if (current) {
        appendMove(current.player, current.move);
        playAiVoice(current.player, current.move);

        // [Mod] Check if we need to trigger a delayed win celebration for this player
        if (pendingWinCelebrationRef.current.has(current.player)) {
           // Check if there are any MORE moves for this player in the queue
           const hasMoreMoves = aiMoveQueueRef.current.some(item => item.player === current.player);
           if (!hasMoreMoves) {
               console.log(`队列清空，触发延迟庆祝: ${current.player}`);
               pendingWinCelebrationRef.current.delete(current.player);
               setTimeout(() => {
                   playWinCelebration(current.player);
               }, 1200);
           }
        }
      }
      lastAiDisplayTimeRef.current = Date.now();

      if (aiMoveQueueRef.current.length === 0) {
        setActiveAiPlayer("");
      } else {
        setActiveAiPlayer(aiMoveQueueRef.current[0]?.player || "");
      }

      processAiQueue();
    };

    if (wait <= 0) {
      showMove();
    } else {
      clearAiTimer();
      aiQueueTimerRef.current = setTimeout(showMove, wait);
    }
  }, [appendMove, clearAiTimer, setAiProcessing, setActiveAiPlayer, playAiVoice, playWinCelebration]);

  const enqueueAiMove = useCallback((player, move) => {
    aiMoveQueueRef.current.push({ player, move });
    if (!aiProcessingRef.current) {
      processAiQueue();
    }
  }, [processAiQueue]);

  // 游戏结束时：立即清空 AI 出牌队列，保证最后几手牌先展示出来再弹结算。
  const flushAiQueue = useCallback(
    ({ playVoice = false } = {}) => {
      // 停止任何延迟展示
      clearAiTimer();
      if (aiQueueTimerRef.current) {
        clearTimeout(aiQueueTimerRef.current);
        aiQueueTimerRef.current = null;
      }

      // 立刻把剩余队列全部渲染出来
      while (aiMoveQueueRef.current.length > 0) {
        const current = aiMoveQueueRef.current.shift();
        if (!current) break;
        appendMove(current.player, current.move);
        if (playVoice) {
          playAiVoice(current.player, current.move);
        }
      }

      aiProcessingRef.current = false;
      setAiProcessing(false);
      setActiveAiPlayer("");
      lastAiDisplayTimeRef.current = Date.now();
    },
    [appendMove, clearAiTimer, playAiVoice]
  );

  const clearReplayTimer = useCallback(() => {
    if (replayTimerRef.current) {
      clearTimeout(replayTimerRef.current);
      replayTimerRef.current = null;
    }
  }, []);

  const closeReplay = useCallback(() => {
    clearReplayTimer();
    setReplayPlaying(false);
    setIsReplayOpen(false);
    setReplayData(null);
    setReplayIndex(-1);
    setReplayError(null);
    if (gameResult) {
      setGameResultVisible(true);
      resultShownForGameRef.current = null;
    } else {
      setGameResultVisible(false);
      if (gameId) {
        resultShownForGameRef.current = gameId;
      }
    }
  }, [clearReplayTimer, gameId, gameResult, setGameResultVisible]);

  const advanceReplay = useCallback(() => {
    if (!replayData) return;
    const history = Array.isArray(replayData.history) ? replayData.history : [];
    const maxIndex = history.length - 1;
    if (maxIndex < 0) return;
    setReplayIndex(prev => {
      if (prev >= maxIndex) return maxIndex;
      const target = replaySkipTarget(history, prev + 1);
      return target >= 0 ? Math.min(target, maxIndex) : maxIndex;
    });
  }, [replayData]);

  // 进度条跳转：拖动 range 用精确下标；「上一轮/下一轮」按钮走折叠跳转
  const scrubReplayTo = useCallback((idx, mode = "exact") => {
    if (!replayData) return;
    const history = Array.isArray(replayData.history) ? replayData.history : [];
    const maxIndex = history.length - 1;
    if (maxIndex < 0) return;
    clearReplayTimer();
    let target = idx;
    if (mode === "skip") target = replaySkipTarget(history, idx);
    else if (mode === "prev") target = replayPrevTarget(history, replayIndex < 0 ? 0 : replayIndex);
    if (target < 0) return;
    target = Math.min(target, maxIndex);
    setReplayIndex(target);
    setReplayPlaying(true);
  }, [replayData, replayIndex, clearReplayTimer]);

  const toggleReplay = useCallback(() => {
    if (!isReplayOpen || replayLoading) return;
    setReplayPlaying(prev => {
      const next = !prev;
      if (!next) {
        clearReplayTimer();
      }
      return next;
    });
  }, [isReplayOpen, replayLoading, clearReplayTimer]);

  const openReplay = useCallback(async () => {
    const targetId = lastCompletedGameId || gameId;
    setGameResultVisible(false);
    if (!targetId) {
      setReplayData(null);
      setReplayIndex(-1);
      setReplayPlaying(false);
      setReplayError("当前没有可复盘的牌局");
      setIsReplayOpen(true);
      return;
    }

    try {
      clearReplayTimer();
      setReplayPlaying(false);
      setReplayError(null);
      setReplayLoading(true);
      // 先进入复盘画面（replayData 尚未就绪，各派生值 null-guard 安全），
      // 让 loading 图标在数据到达前就有展示位，避免点击后画面不变显得无响应。
      setIsReplayOpen(true);
      setReplayIndex(-1);
      const res = await fetch(`/api/${targetId}/replay?token=${encodeURIComponent(gameTokenRef.current)}`);
      if (!res.ok) {
        const rawMessage = await res.text();
        let displayMessage = "复盘数据加载失败，请稍后再试";
        if (rawMessage) {
          let parsedDetail = null;
          try {
            const parsed = JSON.parse(rawMessage);
            parsedDetail = typeof parsed?.detail === "string" ? parsed.detail : null;
          } catch (_) {
            parsedDetail = rawMessage;
          }
          if (typeof parsedDetail === "string" && parsedDetail.trim()) {
            displayMessage = parsedDetail.trim();
          }
        }
        throw new Error(displayMessage);
      }
      const data = await res.json();
      setReplayData(data);
      // 立即落到第 1 手，避免开局后空等 REPLAY_STEP_INTERVAL 才显示第一手
      setReplayIndex(0);
      setReplayPlaying(Boolean(data?.history?.length));
    } catch (err) {
      console.error(err);
      setReplayData(null);
      setReplayIndex(-1);
      setReplayPlaying(false);
      const fallbackMessage = (err && typeof err.message === "string" && err.message.trim())
        ? err.message.trim()
        : "复盘数据加载失败，请稍后再试";
      setReplayError(fallbackMessage);
      setIsReplayOpen(true);
    } finally {
      setReplayLoading(false);
    }
  }, [gameId, lastCompletedGameId, clearReplayTimer, setGameResultVisible]);

  // AI 教练（复盘）入口：按钮已从复盘界面移除（功能保留待今后再优化），
  // 触发逻辑保留，后续重新接入时直接调用 _openCoach()
  const _openCoach = useCallback(async () => {
    if (!aiCoachEnabled) return; // AI 教练未开通：无入口
    const targetId = replayData?.game_id || gameId;
    if (!targetId) return;
    clearReplayTimer(); // 立即停掉复盘轮转定时器，等关闭教练窗口后再继续
    setCoachOpen(true);
    setCoachLoading(true);
    setCoachError(null);
    setCoachCached(false);
    setCoachMessage("");
    try {
      const res = await fetch(`/api/${targetId}/coach?token=${encodeURIComponent(gameTokenRef.current)}`);
      const rawText = await res.text();
      let parsed = null;
      try { parsed = rawText ? JSON.parse(rawText) : null; } catch (_) { parsed = null; }
      if (!res.ok) {
        const detail = parsed && typeof parsed?.detail === "string" ? parsed.detail : "教练分析失败，请稍后再试";
        throw new Error(detail);
      }
      setCoachReviews(Array.isArray(parsed?.reviews) ? parsed.reviews : []);
      setCoachMessage((parsed && typeof parsed.message === "string") ? parsed.message : "");
      setCoachCached(Boolean(parsed?.cached));
    } catch (err) {
      console.error(err);
      setCoachReviews(null);
      setCoachMessage("");
      setCoachError((err && typeof err.message === "string" && err.message.trim()) ? err.message.trim() : "教练分析失败，请稍后再试");
    } finally {
      setCoachLoading(false);
    }
  }, [replayData, gameId, gameTokenRef, clearReplayTimer, aiCoachEnabled]);

  const resetMoveTracking = useCallback(() => {
    processedHistoryCountRef.current = new Map();
    lastProcessedHistoryIdRef.current = -1;
    optimisticPendingCountRef.current = new Map();
    pendingResultForGameRef.current = null;
    finishDetectedAtRef.current = 0;
    finishDetectedForGameRef.current = null;
    aiMoveQueueRef.current = [];
    aiProcessingRef.current = false;
    lastAiDisplayTimeRef.current = Date.now();
    clearAiTimer();
    
    // 清空语音队列
    voiceQueueRef.current = [];
    isPlayingVoiceRef.current = false;
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    // 停止所有TTS
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    setBombTrigger(null);
    setAiProcessing(false);
    
    setRoundMoves(createEmptyRoundMoves());
    setAiProcessing(false);
    setActiveAiPlayer("");
  }, [clearAiTimer, setRoundMoves, setAiProcessing, setActiveAiPlayer]);

  // 开始游戏
  const startGame = async () => {
    try {
      setStatusMsg("正在洗牌...");

      // 1. 立即停止上一局残留的音频/定时器（防止还在播放的声音继续干扰）
      if (winVoiceTimerRef.current) {
        clearTimeout(winVoiceTimerRef.current);
        winVoiceTimerRef.current = null;
      }
      if (gameOverVoiceTimerRef.current) {
        clearTimeout(gameOverVoiceTimerRef.current);
        gameOverVoiceTimerRef.current = null;
      }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }

      // 立即断开旧局的 SSE 连接：startGame 的 await 窗口内 validGameIdRef 仍是旧局 id，
      // 若旧连接仍订阅 wait_for_update，会在这段时间把旧局 finished 状态重放回前端。
      disconnectGameStream();
      if (validGameIdRef.current) validGameIdRef.current = null;
      gameTokenRef.current = null;

      // 注意：winCelebrationPlayedRef 等状态标记不能在这里立即清除！
      // 因为接下来的 fetch 是异步的，组件会在 fetch 等待期间重新渲染。
      // 如果此时清除了标记，而 React 状态中仍保留着上一局“某人已出完牌”的状态，
      // useEffect 就会再次误判为“刚赢了”，从而导致开始游戏瞬间再次播放庆祝音效。
      // 所以状态重置必须放到 setGameId 更新数据的那一刻。

      const res = await fetch(`/api/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(memberLoginEnabled && authToken ? { token: authToken } : {}),
      });
      if (!res.ok) throw new Error("API连不上");
      
      const data = await res.json();
      
      // 2. 数据回来后，原子化更新状态，同时重置追踪标记
      winCelebrationPlayedRef.current.clear();
      hasReceivedHandRef.current = false;
      gameOverSummaryPlayedRef.current = false;

      // [新增] 彻底停止旧的轮询，更新有效ID
      if (pollingTimerRef.current) {
         clearTimeout(pollingTimerRef.current);
         pollingTimerRef.current = null;
      }
      validGameIdRef.current = data.game_id;
      // v2.5：保存该局访问 token，供后续所有 game_id 接口鉴权；持久化供刷新恢复
      gameTokenRef.current = data.token || '';
      if (data.token) {
        sessionStorage.setItem('restoreGameToken', data.token);
      }

      setGameId(data.game_id);
      localScoreAppliedForGameRef.current.delete(data.game_id);
      setMyHand(data.my_hand);
      setHandOrder(data.my_hand); // v3.2：新局硬重置用户排列
      // 重置游戏状态，必须将电脑手牌数重置为非0（27张），防止在第一次轮询前触发“手牌为0即获胜”的误判
      setGameState(prev => ({ 
        ...prev, 
        state: "playing", 
        turn: data.current_turn,
        bot_cards: { RightBot: 27, PartnerBot: 27, LeftBot: 27 } 
      }));
      setGameResult(null);
  setGameResultVisible(false);
  resultShownForGameRef.current = null;
      resetMoveTracking();
      closeReplay();
      setLastCompletedGameId(null);
      setStatusMsg("游戏开始！请出牌。");
    } catch (e) {
      console.error(e);
      setStatusMsg("⚠️ 连接后端失败，请确保 python main.py 正在运行");
    }
  };

  const fetchGameState = useCallback(async () => {
    // 使用 validGameIdRef.current 代替 gameId 来确保原子性一致
    const currentGid = validGameIdRef.current;
    if (!currentGid) return;

    try {
      // 增量轮询：带上 last_seq 避免重复拉取相同状态
      const lastSeq = serverSeqRef.current;
      const tok = gameTokenRef.current;
      const stateUrl = lastSeq > 0
        ? `/api/${currentGid}/state?last_seq=${lastSeq}&token=${encodeURIComponent(tok)}`
        : `/api/${currentGid}/state?token=${encodeURIComponent(tok)}`;
      const res = await fetch(stateUrl);
      
      if (res.status === 404) {
        console.warn(`Game ${currentGid} not found (404), stopping polling.`);
        if (validGameIdRef.current === currentGid) {
            validGameIdRef.current = null;
            if (pollingTimerRef.current) {
                clearTimeout(pollingTimerRef.current);
                pollingTimerRef.current = null;
            }
        }
        return;
      }

      if (res.ok) {
        const data = await res.json();

        // 防跨局误处理：本次请求发起的瞬间 validGameIdRef 可能是旧局 id，
        // 返回时已被换成新局（用户点「再来一局」/「重开新局」/页面恢复）。
        // 此时丢弃这条 stale 响应，避免把旧局状态写回 roundMoves / gameState / 重弹结算。
        if (validGameIdRef.current !== currentGid) {
          console.warn(`[Poll] 丢弃旧局(${currentGid})的响应，当前有效局=${validGameIdRef.current}`);
          return;
        }

        // [Fix] AI 超时本地兜底提醒：后端本手采用本地策略时弹一次性提示
        // 仅在游戏进行中(playing)弹；一局已结束/复盘/清理阶段绝不判断超时，避免停留结束页误弹
        if (data.state === "playing" && data.last_ai_fallback && data.last_ai_fallback.seq !== lastFallbackSeqRef.current) {
          lastFallbackSeqRef.current = data.last_ai_fallback.seq;
          setFallbackToast(true);
          setTimeout(() => setFallbackToast(false), 4000);
        }

        // 增量同步：如果服务端确认状态未变化，跳过处理但仍继续轮询
        let nextDelay = 1500; // 默认（unchanged 时保持当前轮询节奏）
        if (data.unchanged === true) {
          // 即使 seq 没变，也要保持 AI 思考光环的显示
          if (data.turn && data.turn !== "User") {
            if (lastTurnRef.current !== data.turn) {
              lastTurnRef.current = data.turn;
              setTurnStartTime(Date.now());
            }
            if (!aiProcessingRef.current) {
              setActiveAiPlayer(data.turn);
              setAiProcessing(true);
            }
            // AI 思考中：保持中速轮询以渲染思考光环，但避免 250ms 空转打爆后端
            nextDelay = 1500;
          } else if (data.turn === "User") {
            lastTurnRef.current = "User";
            setTurnStartTime(0);
            if (aiProcessingRef.current || lastTurnRef.current) {
              setActiveAiPlayer("");
              setAiProcessing(false);
            }
            // 空闲且无变化：降频
            nextDelay = 3000;
          }
          console.log(`[Poll] unchanged seq=${data.seq} turn=${data.turn} nextDelay=${nextDelay}ms`);
        } else {
          console.log(`[Poll] changed seq=${data.seq} turn=${data.turn} state=${data.state}`);

        // 更新本地 seq 跟踪：后端返回的 seq 是权威当前值，必须无条件同步。
        // 否则 serverSeqRef 卡在旧值（如页面刷新/换局后后端 seq 已重置）时，
        // 因「data.seq > serverSeqRef」不成立而永不修正，导致前端误判 AI 卡死。
        if (typeof data.seq === 'number') {
          serverSeqRef.current = data.seq;
        }
        
        // [Optimization] Stable Hand Update
        setMyHand(currentHand => {
            if (!Array.isArray(data.my_hand)) return currentHand;
            if (pendingPlayCardsCountRef.current > 0) {
                if (data.my_hand.length <= currentHand.length - pendingPlayCardsCountRef.current) {
                    pendingPlayCardsCountRef.current = 0;
                    lastUserPlayTimeRef.current = 0;
                    return data.my_hand;
                }
                return currentHand;
            }
            // Check identity to prevent re-renders
            if (data.my_hand.length === currentHand.length && data.my_hand.every((v, i) => v === currentHand[i])) {
                return currentHand;
            }
            return data.my_hand;
        });

        // v3.2：合并用户排列与服务器手牌（保留已存在卡的相对顺序，新卡追加；整体替换自动重置）
        setHandOrder(prev => reconcileHandOrder(prev, data.my_hand));

        // [Optimization] Stable State Update (Only if changed)
        setGameState(prev => {
           const next = {
             state: data.state,
             turn: data.turn,
             last_move: data.last_move,
             last_player: data.last_player,
             bot_cards: data.bot_cards_count
           };
           const isSame = prev.state === next.state && 
                        prev.turn === next.turn && 
                        prev.last_move === next.last_move &&
                        prev.last_player === next.last_player &&
                        JSON.stringify(prev.bot_cards) === JSON.stringify(next.bot_cards);
           return isSame ? prev : next;
        });
        
        const serverUserId = Number.isFinite(data?.user_id) ? data.user_id : null;
        const serverLoggedIn = Boolean(data?.logged_in && serverUserId !== null);
        // v3 修复：/api/score 类接口对会员登录态不感知（logged_in 恒 false）。
        // 若本地存在有效会员 auth 会话（sessionStorage authToken），不能被这里重置回 false。
        const hasAuthSession = Boolean(authTokenRef.current);
        if (serverLoggedIn) {
          setIsLoggedIn(true);
          setCurrentUserId(serverUserId);
          if (data.total_scores) {
            setTotalScores(prev => {
              const isSame = JSON.stringify(prev) === JSON.stringify(data.total_scores);
              return isSame ? prev : data.total_scores;
            });
          }
          if (lastSyncedUserIdRef.current !== serverUserId) {
            lastSyncedUserIdRef.current = serverUserId;
            syncLocalScores(serverUserId);
          }
        } else if (!sessionLoggedInRef.current && !hasAuthSession) {
          // isLoggedIn 已从 fetchGameState 依赖剔除，此处直接幂等置 false
          setIsLoggedIn(false);
          setCurrentUserId(null);
          lastSyncedUserIdRef.current = null;
          setTotalScores(loadLocalScores());
        }
        
        // Status checks
        if (data.turn && data.turn !== "User") {
          // [New] 记录当前回合开始时间
          if (lastTurnRef.current !== data.turn) {
            lastTurnRef.current = data.turn;
            setTurnStartTime(Date.now());
          }
          if (!aiProcessingRef.current) {
            setActiveAiPlayer(data.turn);
            setAiProcessing(true);
          }
        } else if (data.turn === "User") {
          lastTurnRef.current = "User";
          setTurnStartTime(0);
          setActiveAiPlayer("");
          setAiProcessing(false);
        }

        // --- Core Logic: Processing History ---
        if (Array.isArray(data.recent_history)) {
          const chronological = data.recent_history;
          const hasHid = chronological.some(h => Number.isFinite(h?._hid));

          if (hasHid) {
            // Processing based on _hid (monotonic ID)
            const sorted = [...chronological].sort((a, b) => (a?._hid ?? 0) - (b?._hid ?? 0));
            sorted.forEach(item => {
                const hid = Number.isFinite(item?._hid) ? item._hid : null;
                if (hid !== null && hid <= lastProcessedHistoryIdRef.current) return;

                const normalized = {
                  player: item.player,
                  action: item.action,
                  desc: item.desc,
                  cards: item.cards || []
                };

                const key = makeHistoryKey(normalized);
                if (normalized.player === "User") {
                  const pending = optimisticPendingCountRef.current.get(key) || 0;
                  if (pending > 0) {
                    optimisticPendingCountRef.current.set(key, pending - 1);
                    if (hid !== null) lastProcessedHistoryIdRef.current = hid;
                    return;
                  }
                }

                if (normalized.player) {
                  enqueueAiMove(normalized.player, normalized);
                }
                if (hid !== null) lastProcessedHistoryIdRef.current = hid;
              });
          } else {
            // Fallback for old backends (less efficient)
            const batchCounts = {};
            chronological.forEach(item => {
              const normalized = {
                player: item.player,
                action: item.action,
                desc: item.desc,
                cards: item.cards || []
              };
              const key = makeHistoryKey(normalized);
              batchCounts[key] = (batchCounts[key] || 0) + 1;
              const occurrence = batchCounts[key];
              const processedCount = processedHistoryCountRef.current.get(key) || 0;
              if (occurrence > processedCount) {
                if (normalized.player) enqueueAiMove(normalized.player, normalized);
                processedHistoryCountRef.current.set(key, occurrence);
              }
            });
          }
        } 
        
        // Game Finish Logic
        if (data.state === "finished") {
          if (currentGid && finishDetectedForGameRef.current !== currentGid) {
            finishDetectedForGameRef.current = currentGid;
            finishDetectedAtRef.current = Date.now();
            // 一局结束：清空 AI 超时兜底提醒状态，避免复盘时因 seq 变化误弹超时提示
            lastFallbackSeqRef.current = null;

            // 每日局数：仅在本局首次判定 finished 时结算一次（与后端 play_counted 幂等对齐）。
            // 本地不限制实际玩牌局数——会员登录开通时，仅展示服务器记录的当日局数（官网权威，
            // 达 MEMBER_LIMIT 后一次性软提醒，仍可继续玩）；未开通/游客不计数不拦截。
            if (memberLoginEnabledRef.current && authTokenRef.current) {
              // 会员：拉取服务器记录的当日局数/得分（远程模式走官网，本地模式走本地 store）
              fetchMe(authTokenRef.current).then((info) => {
                if (info) {
                  setAuthUser({ nickname: info.nickname, email: info.email, plays_today: info.plays_today, limit: info.limit });
                  try {
                    window.sessionStorage.setItem("authUser", JSON.stringify({ nickname: info.nickname, email: info.email }));
                  } catch (_) { /* ignore */ }
                  if (info.plays_today >= info.limit) setMemberQuotaToast(true);
                }
              });
            }
          }
          setStatusMsg("🏆 游戏结束！");
          
          if (data.result) {
            setGameResult(data.result);
            if (currentGid && !localScoreAppliedForGameRef.current.has(currentGid)) {
              const scores = data.result?.scores || {};
              // 优先尝试在线上传（只要前端认为已登录或后端返回已登录）
              const shouldUpload = serverLoggedIn || isLoggedIn;
              
              if (shouldUpload) {
                uploadGameScore(scores).then(success => {
                  if (!success) {
                    // 上传失败（如网络中断或Session失效），降级为本地存储
                    console.log("Upload failed, falling back to local storage");
                    applyLocalScoreDelta(scores);
                  }
                });
              } else {
                applyLocalScoreDelta(scores);
              }
              localScoreAppliedForGameRef.current.add(currentGid);
            }
            const isAlreadyShown = (resultShownForGameRef.current === currentGid);
            if (!isAlreadyShown) {
              const historyLen = typeof data.history_len === "number" ? data.history_len : null;
              const expectLastHid = historyLen !== null ? historyLen - 1 : null;
              const historyCaughtUp = expectLastHid === null ? true : lastProcessedHistoryIdRef.current >= expectLastHid;
              const duration = finishDetectedAtRef.current > 0 ? (Date.now() - finishDetectedAtRef.current) : 0;
              const finishedLongEnough = duration >= FINISH_SETTLE_MS;
              const aiQueueEmpty = !aiProcessingRef.current && (aiMoveQueueRef.current.length === 0);

              if ((historyCaughtUp && aiQueueEmpty && finishedLongEnough) || (!isReplayOpenRef.current && duration > 4000) || (isReplayOpenRef.current && aiQueueEmpty)) {
                resultShownForGameRef.current = currentGid;
                flushAiQueue({ playVoice: false });
                voiceQueueRef.current = [];
                isPlayingVoiceRef.current = false;
                
                const delay = Math.max(SHOW_RESULT_AFTER_FLUSH_MS, 0);
                setTimeout(() => setGameResultVisible(true), delay);
              }
            }
          }
          currentGid && setLastCompletedGameId(prev => (prev ?? currentGid));

          // [Opt] 一局已结束且历史已处理：结算数据已完整取得，停止对局轮询。
          // 若用户仍停留在结算页/复盘，也不再有状态变化需要拉取，避免持续打 /state。
          if (pollingTimerRef.current) {
            clearTimeout(pollingTimerRef.current);
            pollingTimerRef.current = null;
          }
        } else {
          setGameResultVisible(false);
          resultShownForGameRef.current = null;
          finishDetectedAtRef.current = 0;
          finishDetectedForGameRef.current = null;
        }

        // [Adaptive Polling Delay Calculation]
        nextDelay = 1500; // AI 出牌中（中速轮询，AI 思考通常 1-3 秒，无需 250ms 高频空转）
        if (data.turn === "User" && aiMoveQueueRef.current.length === 0) {
            nextDelay = 2000;
        } else if (data.state === "finished") {
            nextDelay = 5000;
        }
        } // end of else (data.unchanged !== true)

        // [Opt] finished 状态：结算弹窗显示前仍低速轮询（5s/次），
        // 让弹窗条件（duration≥FINISH_SETTLE_MS 且 AI 队列排空）能被再次求值。
        // 若这里也停，弹窗就只能靠 SSE 重连重推 finished 快照——而 SSE 稳定后不会重连，
        // 会导致一局结束后弹窗永不出现，60s 后被误判为「AI 卡死」。
        if (validGameIdRef.current === currentGid && !gameResultVisibleRef.current) {
            pollingTimerRef.current = setTimeout(fetchGameState, nextDelay);
        }
        return data?.turn || null;
      }
    } catch (e) {
      console.warn('[Poll] 轮询异常，3秒后重试:', e?.message || e);
      // 异常后恢复轮询，避免网络抖动导致轮询静默死亡
      if (validGameIdRef.current === currentGid && !gameResultVisibleRef.current) {
        pollingTimerRef.current = setTimeout(fetchGameState, 3000);
      }
    }
    // isLoggedIn / activeAiPlayer / aiProcessing 是会在对局中反复变化的 state，
    // 放入依赖会使 fetchGameState 引用漂移 → connectGameStream 重建 → SSE 反复重连。
    // 这些值此处仅作「当前是否已在登录/处理中」的防抖判断，改用 ref 读取后即可从依赖剔除。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enqueueAiMove, makeHistoryKey, flushAiQueue, applyLocalScoreDelta, syncLocalScores, uploadGameScore, setGameResultVisible]);

  // 2.1 v2.4 SSE：用 EventSource 替代空闲轮询。收到事件即拉一次最新状态
  //      （复用 fetchGameState 的全部处理逻辑），空闲时不发任何请求。
  //      连接失败自动降级回 fetchGameState 轮询。
  // 2. 轮询状态与桌面出牌逻辑（v2.4：优先 SSE，失败降级轮询）
  //    connectGameStream 已抽到 hooks/useGameStream.js
  //    v2.5：SSE 的 /stream 需带 token（EventSource 无法设 header，只能走 query）
  const getGameToken = useCallback(() => gameTokenRef.current || '', []);
  const { connectGameStream, disconnectGameStream } = useGameStream(fetchGameState, getGameToken);
  useEffect(() => {
    if (gameId) {
        // 清除旧的轮询定时器
        if (pollingTimerRef.current) {
            clearTimeout(pollingTimerRef.current);
            pollingTimerRef.current = null;
        }
        // 只有当 gameId === validGameIdRef.current 时才启动，防止滞后干扰
        if (validGameIdRef.current === gameId) {
            // 优先用 SSE 实时推送；SSE 内部会在失败时降级为 fetchGameState 轮询
            connectGameStream(gameId);
        }
    } else {
        // 一局结束/重置（gameId 置空）：主动断开旧 SSE 并清空有效 ID，
        // 否则旧连接仍订阅 wait_for_update，会在「再来一局」窗口期重放旧局 finished 状态，
        // 把旧局最后一轮重新写回 roundMoves 并再次弹出结算。
        disconnectGameStream();
        if (validGameIdRef.current) validGameIdRef.current = null;
    }

    return () => {
        if (pollingTimerRef.current) {
            clearTimeout(pollingTimerRef.current);
            pollingTimerRef.current = null;
        }
        // SSE 连接由 useGameStream hook 自行在卸载时关闭；
        // gameId 变化到新局时，connectGameStream 内部会先断开旧连接。
    };
  }, [gameId, fetchGameState, connectGameStream, disconnectGameStream]);

/*
  // 原有的 setInterval 逻辑已被上面的 fetchGameState 替代
  useEffect(() => {
    if (!gameId) return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/${gameId}/state`);
        if (res.ok) {
           // ... logic ...
*/

  useEffect(() => {
    return () => {
      clearAiTimer();
    };
  }, [clearAiTimer]);

  useEffect(() => {
    if (!isReplayOpen || !replayPlaying || !replayData) {
      clearReplayTimer();
      return;
    }

    // AI 教练窗口打开时暂停复盘轮转；关闭后 effect 重跑，从当前位置继续
    if (coachOpen) {
      clearReplayTimer();
      return;
    }

    const totalMoves = Array.isArray(replayData.history) ? replayData.history.length : 0;
    if (totalMoves === 0) {
      clearReplayTimer();
      setReplayPlaying(false);
      return;
    }

    if (replayIndex >= totalMoves - 1) {
      clearReplayTimer();
      setReplayPlaying(false);
      return;
    }

    if (replayIndex < 0) {
      clearReplayTimer();
      replayTimerRef.current = setTimeout(advanceReplay, REPLAY_STEP_INTERVAL);
      return;
    }

    clearReplayTimer();
    replayTimerRef.current = setTimeout(advanceReplay, REPLAY_STEP_INTERVAL);

    return () => {
      clearReplayTimer();
    };
  }, [isReplayOpen, replayPlaying, replayData, replayIndex, coachOpen, advanceReplay, clearReplayTimer]);

  useEffect(() => {
    return () => {
      clearReplayTimer();
    };
  }, [clearReplayTimer]);

  // 出牌请求：快路径直接携带 card_ids，后端会从手牌推导/校验牌型（无需 move_id 与
  // 合法列表，后端 /api/play 的 card_ids 分支只对选中牌做小规模枚举，不重算全量）。
  // 网络抗丢包：带请求ID和重试的出牌请求（每次6秒超时）
  // [优化] 出牌卡「处理中」主因：单次超时10s×3次重试+退避≈32s最坏卡死。
  // 后端 /api/play 本身 fire-and-forget 秒回，真正耗时在重试外壳；收紧到
  // 6s×1次重试≈13s最坏，且成功一次立即返回。
  const resilientPlayRequest = async (gameId, moveId, cardIds, retries = 1) => {
    const requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    let lastError = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
      if (attempt > 0) {
        console.log(`[Retry] 第 ${attempt} 次重试出牌 requestId=${requestId}`);
        await new Promise(r => setTimeout(r, 1000));
      }

      const controller = new AbortController();
      // 每轮 5s：请求+响应头+响应体读取都受这一个窗口约束（body 读完才清 timer）。
      // 二次尝试 5s + 1s 退避 ≈ 11s 最坏，成功一次立即返回。
      const timeoutId = setTimeout(() => controller.abort(), 5000);

      try {
        const res = await fetch(`/api/play`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ game_id: gameId, move_id: moveId, card_ids: cardIds, request_id: requestId, token: gameTokenRef.current }),
          signal: controller.signal
        });
        // 幂等重试安全：后端按 request_id 去重，重复请求返回 duplicate 而非重复执行。
        const data = await res.json().catch(() => null);
        clearTimeout(timeoutId);
        if (res.ok) return { res, data };
        // 非 2xx：不重试（幂等响应/校验错误是权威结果），直接把结果交给调用方处理
        return { res, data };
      } catch (e) {
        clearTimeout(timeoutId);
        lastError = e;
        console.warn(`[Network] 出牌请求失败 (attempt ${attempt + 1}/${retries + 1}):`, e.message);
      }
    }

    throw lastError || new Error("出牌请求失败，请检查网络连接");
  };

  // [优化] 带超时的 fetch：/moves 兜底/校验请求不再可能无限等待拖长「处理中」
  // v3.3 修复「一直卡处理中」：单个 6s 超时窗口覆盖「连接+响应头+响应体读取」
  // 全链路。timer 在 json() 读完 body 后才清除；若调用方提前 return 未读 body，
  // timer 到点 abort 一个已完成的 fetch，是无害空操作。响应体读取因此也不会悬挂。
  const fetchWithTimeout = async (url, options = {}, timeoutMs = 6000) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(url, { ...options, signal: controller.signal });
    return {
      res,
      json: async () => {
        try {
          const text = await res.text();
          try { return JSON.parse(text); } catch (_) { return null; }
        } finally {
          clearTimeout(timeoutId);
        }
      },
    };
  };

  const playCards = async () => {
    if (!gameId || isActionPending) return;
    const selectedCardIds = [...selected];
    if (selectedCardIds.length === 0) return;
    setIsActionPending(true);
    setStatusMsg("正在校验牌型...");

    try {
      // [优化] 出牌链路从「两次串行请求」收敛为「一次」：直接以 card_ids 发 /api/play，
      // 后端 card_ids 分支对选中牌做小规模枚举推导 + 合法性校验（是否在手中/牌型/能否管上），
      // 不需要前端先拉 /moves 取列表。这样「处理中」只剩一次请求，且不再重复全量枚举的 CPU 开销。
      // 成功响应里的 user_result.move.desc 即为后端权威的牌型描述，用于乐观更新展示。
      setStatusMsg("正在出牌...");
      const { res: playRes, data: playData } = await resilientPlayRequest(gameId, null, selectedCardIds);

      if (!playRes.ok) {
        const errMsg = await parseApiError(playRes);
        if (errMsg && errMsg.includes("not in hand")) {
          setStatusMsg("⚠️ 手牌数据过期(已自动刷新)，请重新出牌");
          setSelected([]);
          pendingPlayCardsCountRef.current = 0;
          return;
        }
        setStatusMsg(`⚠️ 出牌失败：${errMsg || '请检查是否轮到你/牌型是否有效'}`);
        playErrorVoice(errMsg || "无效操作");
        return;
      }

      // 幂等重试：请求已处理过，跳过乐观更新（避免重复出牌动画）
      // 注意：超时窗口可能落在 body 读取阶段，此时 res.ok 为 true 但 data 为 null——
      // 后端很可能已真实打出这张牌，只是响应体没读完。此时**绝不能只提示后 return**：
      // 否则手牌/selected 停在旧状态，用户再点出牌会撞后端「牌已不在手」→ 反复报
      // 「牌型不匹配」，看起来像「无法继续出牌」。正确做法是立即拉一次真实状态自愈，
      // 并清空选中让用户基于同步后的真实手牌重选。
      if (playData === null) {
        pendingPlayCardsCountRef.current = 0; // 清除「待处理牌数」占位，否则会锁住后续手牌更新
        if (gameId && !isReplayOpenRef.current) {
          setStatusMsg("⚠️ 出牌结果未确认，正在同步最新牌局...");
          await fetchGameState();
        }
        // 结果不确定：清空选中，让用户基于同步后的真实手牌重新选择。
        // 不清的话，若牌已实际打出，selected 残留已失效牌 → 再次出牌撞「牌已不在手」→
        // 反复「牌型不匹配」死锁。清空后从新手里重选，绝对安全。
        setSelected([]);
        setStatusMsg("⚠️ 出牌结果未确认，请重新出牌或等待状态刷新");
        return;
      }
      if (playData.status === "duplicate") {
        console.log(`[Play] 重复请求已忽略 requestId, seq=${playData.seq}`);
        if (typeof playData.seq === 'number') {
          serverSeqRef.current = playData.seq;
        }
        setStatusMsg("✅ 出牌成功！");
        return;
      }

      if (playData.error) {
        // [竞态修复] 若后端已推进到 AI 回合而前端还显示「轮到你了」，出牌会被拒为
        // "Not your turn"。此处不摆「出牌失败」，而是立即拉一次真实状态自愈：
        // 更新 turn/hand/seq，让 UI 与服务器回合一致，AI 结束后自动轮到用户。
        if (playData.error.toLowerCase().includes("turn")) {
          if (gameId && !isReplayOpenRef.current) {
            const freshTurn = await fetchGameState();
            setStatusMsg(
              `😅 当前是${freshTurn && freshTurn !== "User" ? (PLAYER_DISPLAY_NAMES[freshTurn] || freshTurn) : "对方"}的回合，等 TA 出牌后轮到你`
            );
            if (freshTurn && freshTurn !== "User") {
              setActiveAiPlayer(freshTurn);
              setAiProcessing(true);
            }
          } else {
            setStatusMsg("😅 当前不是你的回合，请等 AI 出牌");
          }
        } else {
          setStatusMsg(`⚠️ 出牌失败：${playData.error}`);
          playErrorVoice(playData.error);
        }
        return;
      }

      // 更新本地 seq 跟踪
      if (typeof playData.seq === 'number') {
        serverSeqRef.current = playData.seq;
      }

      // [竞态修复] 后端权威地告知下一回合：出牌成功后若立即轮到 Bot（AI 思考中），
      // 立刻翻转本地 turn 并点亮 AI 光环，避免「轮到你了」残留到下一次轮询
      // （此前 ~1-2s 窗口里用户会再次出牌 → 被后端拒为 "Not your turn"）。
      const nextTurn = playData?.current_state?.turn;
      if (nextTurn) {
        lastTurnRef.current = nextTurn;
        if (nextTurn === "User") {
          setTurnStartTime(0);
          setActiveAiPlayer("");
          setAiProcessing(false);
        } else {
          setTurnStartTime(Date.now());
          setActiveAiPlayer(nextTurn);
          setAiProcessing(true);
          setGameState(prev => (prev.turn === nextTurn ? prev : { ...prev, turn: nextTurn }));
        }
      }

      // 乐观更新：立刻在本地显示自己的出牌，并记录哈希避免重复。
      // desc 优先用后端权威的牌型描述（user_result.move.desc），取不到再回退通用文案。
      const authoritativeDesc = playData?.user_result?.move?.desc;
      const optimisticMove = {
        action: "PLAY",
        desc: authoritativeDesc || "出牌",
        cards: [...selectedCardIds]
      };
      const optimisticHashEntry = {
        player: "User",
        ...optimisticMove
      };
      const optimisticKey = makeHistoryKey(optimisticHashEntry);
      const processedCount = processedHistoryCountRef.current.get(optimisticKey) || 0;
      processedHistoryCountRef.current.set(optimisticKey, processedCount + 1);
      const pendingCount = optimisticPendingCountRef.current.get(optimisticKey) || 0;
      optimisticPendingCountRef.current.set(optimisticKey, pendingCount + 1);
      appendMove("User", optimisticMove);
      playAiVoice("User", optimisticMove);
      removePlayedFromHand(selectedCardIds);
      lastUserPlayTimeRef.current = Date.now();
      pendingPlayCardsCountRef.current = selectedCardIds.length; // 记录待处理牌数
      setSelected([]);
      setStatusMsg("✅ 出牌成功！");

    } catch (e) {
      console.error(e);
      setStatusMsg(`⚠️ 网络错误：${e?.message || ''}`);
    } finally {
      setIsActionPending(false);
    }
  };

  // 4. 用户 Pass
  const passTurn = async () => {
    if (!gameId || isActionPending) return;
    setIsActionPending(true);
    try {
       // [优化] PASS 同样走带超时的 /moves 预取，避免无超时 fetch 拖长「处理中」
       const movesRes = await fetchWithTimeout(`/api/${gameId}/moves?token=${encodeURIComponent(gameTokenRef.current)}`);
       const data = await movesRes.json();
  const passMove = data?.moves?.find(m => m.desc === "PASS" || m.desc === "不出");

       if (!passMove) {
         setPassShake(true);
         setTimeout(() => setPassShake(false), 500);
         return;
       }

       // v3.3 修复「一直卡处理中」：原处用裸 fetch 发 /api/play，无任何超时——
       // 若请求悬挂（服务端重启/网络黑洞/代理停滞），isActionPending 永不恢复，
       // 出牌/不出按钮永远置灰。现在走带 6s 超时的 fetchWithTimeout，超时立即
       // 抛出 AbortError 并清除 pending；同时带 request_id 幂等，超时后用户重试
       // 不会重复执行 PASS。
       const passPlayRes = await fetchWithTimeout(`/api/play`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ game_id: gameId, move_id: passMove.id, request_id: `pass-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`, token: gameTokenRef.current })
       });

       if (!passPlayRes.res.ok) {
         const errMsg = await parseApiError(passPlayRes.res);
         setStatusMsg(`⚠️ PASS失败：${errMsg || '请检查是否轮到你/当前是否允许PASS'}`);
         return;
       }
       const passData = passPlayRes.json ? await passPlayRes.json() : null;
       // [竞态修复] 与出牌一致：PASS 被拒为「不是你的回合」时立即拉一次真实状态自愈
       if (passData?.error && passData.error.toLowerCase().includes("turn")) {
         if (gameId && !isReplayOpenRef.current) {
           const freshTurn = await fetchGameState();
           setStatusMsg(
             `😅 当前是${freshTurn && freshTurn !== "User" ? (PLAYER_DISPLAY_NAMES[freshTurn] || freshTurn) : "对方"}的回合，等 TA 出牌后轮到你`
           );
           if (freshTurn && freshTurn !== "User") {
             setActiveAiPlayer(freshTurn);
             setAiProcessing(true);
           }
         } else {
           setStatusMsg("😅 当前不是你的回合，请等 AI 出牌");
         }
         return;
       }

  const passMoveData = { action: "PASS", desc: "PASS", cards: [] };
  const passKeyEntry = { player: "User", action: "PASS", desc: "PASS", cards: [] };
  const passKey = makeHistoryKey(passKeyEntry);
  const processedCount = processedHistoryCountRef.current.get(passKey) || 0;
  processedHistoryCountRef.current.set(passKey, processedCount + 1);
  const pendingCount = optimisticPendingCountRef.current.get(passKey) || 0;
  optimisticPendingCountRef.current.set(passKey, pendingCount + 1);
  appendMove("User", passMoveData);
  playAiVoice("User", passMoveData);
       setSelected([]);
       setStatusMsg("已跳过");
     } catch(e) {
       console.error(e);
       setStatusMsg(`⚠️ PASS 失败：${e?.name === 'AbortError' ? '请求超时，请重试' : (e?.message || '')}`);
     } finally {
       setIsActionPending(false);
     }
  };

  const _replayPlayers = useMemo(() => {
    if (replayData?.players?.length) return replayData.players;
    if (replayData?.initial_hands) return Object.keys(replayData.initial_hands);
    return ["User", "RightBot", "PartnerBot", "LeftBot"];
  }, [replayData]);

  const currentReplayMove = useMemo(() => {
    if (!replayData || replayIndex < 0) return null;
    const history = Array.isArray(replayData.history) ? replayData.history : [];
    return history[replayIndex] || null;
  }, [replayData, replayIndex]);

  // 复盘：只显示「出牌一方」的手牌，其余玩家不显示，避免信息混乱。
  // 未开始（index<0）时默认显示我方手牌，方便先看初始牌。
  const activeReplayPlayer = useMemo(() => {
    if (!isReplayOpen) return null;
    return currentReplayMove?.player || "User";
  }, [isReplayOpen, currentReplayMove]);

  const replayMovesLog = useMemo(() => {
    if (!replayData || replayIndex < 0) return [];
    const history = Array.isArray(replayData.history) ? replayData.history : [];
    return history.slice(0, replayIndex + 1);
  }, [replayData, replayIndex]);

  const replayHands = useMemo(() => {
    if (!replayData?.initial_hands) return null;
    const snapshot = {};
    Object.entries(replayData.initial_hands).forEach(([player, cards]) => {
      snapshot[player] = [...cards];
    });
    if (!replayData.history || replayIndex < 0) {
      return snapshot;
    }
    for (let i = 0; i <= replayIndex && i < replayData.history.length; i++) {
      const move = replayData.history[i];
      if (!move || move.action !== "PLAY" || !Array.isArray(move.cards)) continue;
      const removal = new Set(move.cards);
      const remaining = snapshot[move.player] ? [...snapshot[move.player]] : [];
      snapshot[move.player] = remaining.filter(card => !removal.has(card));
    }
    return snapshot;
  }, [replayData, replayIndex]);

  const replayComplete = useMemo(() => {
    if (!replayData) return false;
    const total = Array.isArray(replayData.history) ? replayData.history.length : 0;
    return total > 0 && replayIndex >= total - 1;
  }, [replayData, replayIndex]);

  const nextReplayMove = useMemo(() => {
    if (!replayData) return null;
    const history = Array.isArray(replayData.history) ? replayData.history : [];
    if (history.length === 0) return null;
    if (replayIndex < 0) return history[0];
    if (replayIndex + 1 < history.length) return history[replayIndex + 1];
    return null;
  }, [replayData, replayIndex]);

  const replayTotals = useMemo(() => {
    const total = Array.isArray(replayData?.history) ? replayData.history.length : 0;
    const step = total > 0 ? Math.min(replayIndex + 1, total) : 0;
    return { total, step };
  }, [replayData, replayIndex]);

  const replayErrorText = useMemo(() => {
    if (!replayError) return "";
    if (typeof replayError === "string") {
      const trimmed = replayError.trim();
      return trimmed || "复盘数据异常";
    }
    if (replayError instanceof Error && typeof replayError.message === "string") {
      const trimmed = replayError.message.trim();
      if (trimmed) return trimmed;
    }
    if (replayError?.message && typeof replayError.message === "string") {
      const trimmed = replayError.message.trim();
      if (trimmed) return trimmed;
    }
    return "复盘数据异常";
  }, [replayError]);

  const displayRoundMoves = useMemo(() => {
    if (!isReplayOpen) return roundMoves;
    const mapped = createEmptyRoundMoves();
    for (let i = replayMovesLog.length - 1; i >= 0; i--) {
      const move = replayMovesLog[i];
      if (!move || !move.player || !mapped[move.player]) continue;
      mapped[move.player].push({
        action: move.action,
        desc: move.desc || (move.action === "PASS" ? "PASS" : move.action),
        cards: Array.isArray(move.cards) ? move.cards : []
      });
    }
    return mapped;
  }, [isReplayOpen, replayMovesLog, roundMoves]);

  const displayHands = useMemo(() => {
    if (!isReplayOpen || !replayHands) return null;
    return replayHands;
  }, [isReplayOpen, replayHands]);

  const displayBotCounts = useMemo(() => {
    if (!isReplayOpen || !replayHands) return gameState.bot_cards;
    return {
      RightBot: replayHands.RightBot ? replayHands.RightBot.length : 0,
      PartnerBot: replayHands.PartnerBot ? replayHands.PartnerBot.length : 0,
      LeftBot: replayHands.LeftBot ? replayHands.LeftBot.length : 0
    };
  }, [isReplayOpen, replayHands, gameState.bot_cards]);

  const leftHasMoves = displayRoundMoves?.LeftBot?.length > 0;
  const rightHasMoves = displayRoundMoves?.RightBot?.length > 0;

  // [Fix] 四个玩家的已出牌位可能重叠：左右家竖排牌扇向上/下家出牌区伸展时，
  // 若按固定 z 层级（上/下家 z20 > 桌面 z10）会让 User/对家永远盖住左右家，
  // 与「最新出牌者应盖住其他人」的直觉相反。这里推导最新出牌者，动态抬高其 z。
  // 注意：CSS 层叠中，同一 stacking context 内 z-index 才可直接比较；父级 context 的
  // z 永远压过子级（实测 partner z45/z20ctx 盖住 left z60/z10ctx）。因此四个出牌位
  // 必须处于同一 context（区段不再设 z，且手牌区包进 z-[20]），此处的 z 才有效。
  // 每手的时间戳来自 appendMove（Date.now()）；复盘模式下最近一手即最新。
  const mostRecentPlayedPlayer = useMemo(() => {
    const candidates = ["User", "RightBot", "PartnerBot", "LeftBot"];
    let bestPlayer = null;
    let bestTs = -Infinity;
    candidates.forEach(p => {
      const moves = displayRoundMoves?.[p] || [];
      const ts = moves[0]?.ts;
      if (typeof ts === "number" && ts > bestTs) {
        bestTs = ts;
        bestPlayer = p;
      }
    });
    if (bestPlayer) return bestPlayer;
    // 时间戳缺失（旧数据/复盘）时退化为固定优先顺序：右侧家 > 下家 > 上家
    if ((displayRoundMoves?.RightBot || []).length > 0) return "RightBot";
    if ((displayRoundMoves?.User || []).length > 0) return "User";
    if ((displayRoundMoves?.PartnerBot || []).length > 0) return "PartnerBot";
    if ((displayRoundMoves?.LeftBot || []).length > 0) return "LeftBot";
    return null;
  }, [displayRoundMoves]);

  // [Fix] 各玩家出牌位的 z-index 配置：谁最新出牌谁在最上层，
  // 其余按「右侧家(RightBot) > 下家(User) > 上家(PartnerBot) > 左家(LeftBot)」的直觉层级。
  const playedBubbleZ = useMemo(() => {
    const zOrder = { RightBot: 50, User: 45, PartnerBot: 40, LeftBot: 35 };
    const z = { ...zOrder };
    if (mostRecentPlayedPlayer) {
      z[mostRecentPlayedPlayer] = 60;
    }
    return z;
  }, [mostRecentPlayedPlayer]);

  const displayedMyHand = useMemo(() => {
    if (isReplayOpen && replayHands) return replayHands.User || [];
    // v3.2：合并用户手动排列与服务器手牌（服务器顺序仍为出牌权威）
    return reconcileHandOrder(handOrder, myHand);
  }, [isReplayOpen, replayHands, myHand, handOrder]);

  const turnStatusMessage = useMemo(() => {
    if (isReplayOpen) return "";

    const priorityStatuses = ["⚠️", "❌"];
    if (priorityStatuses.some(prefix => statusMsg.startsWith(prefix))) {
      return statusMsg;
    }

    if (gameState.state === "finished") {
      return statusMsg;
    }

    if (!gameState.turn) {
      return statusMsg;
    }

    if (aiProcessing && activeAiPlayer && activeAiPlayer !== "User") {
      const readable = PLAYER_DISPLAY_NAMES[activeAiPlayer] || activeAiPlayer;
      return `${readable}出牌思考中...`;
    }

    if (gameState.turn === "User") {
      return "👉 轮到你了！请出牌";
    }

    if (gameState.turn) {
      const readable = PLAYER_DISPLAY_NAMES[gameState.turn] || gameState.turn;
      return `${readable}出牌思考中...`;
    }

    return statusMsg;
  }, [isReplayOpen, statusMsg, gameState.state, gameState.turn, aiProcessing, activeAiPlayer]);

  const statusText = turnStatusMessage || statusMsg;


  const replayStatusText = useMemo(() => {
    if (!isReplayOpen) return "";
    if (replayErrorText) return replayErrorText;
    if (replayLoading) return "正在加载复盘数据…";
    if (replayComplete) return "复盘结束";
    return replayPlaying ? "复盘播放中" : "复盘已暂停";
  }, [isReplayOpen, replayErrorText, replayLoading, replayComplete, replayPlaying]);

  const replayProgressText = useMemo(() => {
    if (!isReplayOpen) return "";
    if (replayErrorText) return "";
    if (!replayTotals.total) return "暂无出牌记录";
    return `第 ${replayTotals.step}/${replayTotals.total} 手`;
  }, [isReplayOpen, replayErrorText, replayTotals]);

  const replayHeaderLine = useMemo(() => {
    if (!isReplayOpen) return "";
    if (replayErrorText) return `复盘模式 · ${replayErrorText}`;
    if (replayProgressText) return `复盘模式 · ${replayProgressText}`;
    return "复盘模式";
  }, [isReplayOpen, replayErrorText, replayProgressText]);

  const toggleCard = (id) => {
    if (isReplayOpen) return;
    if (!id) return; // 防止无效ID
    
    if (selected.includes(id)) setSelected(selected.filter(x => x !== id));
    else setSelected([...selected, id]);
  };

  const tableProps = {
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
  };

  const replayProgressElement = useMemo(() => {
    if (!isReplayOpen || !replayTotals.total) return null;
    return (
      <ReplayProgress
        currentIndex={replayIndex}
        total={replayTotals.total}
        progressText={replayProgressText}
        statusText={replayStatusText}
        onScrub={scrubReplayTo}
      />
    );
  }, [isReplayOpen, replayTotals, replayIndex, replayProgressText, replayStatusText, scrubReplayTo]);

  const scorePanelProps = {
    isReplayOpen,
    handleScoreCardClick,
    scoreFlipFace,
    totalScores,
    isLoggedIn,
    userName,
    startPage: !gameId,
    memberLoginEnabled,
  };
  return (
    <div className="h-screen h-[100dvh] bg-table text-slate-100 flex flex-col overflow-hidden font-sans select-none relative">
      {/* ================= AI 卡死提示 + 手动重开（不自动开新局） ================= */}
      {aiStuck && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-slate-900 border border-amber-500 rounded-2xl px-7 py-6 text-center shadow-2xl max-w-[90vw]">
            <div className="text-amber-300 text-lg font-semibold mb-2">⚠️ AI 出牌似乎卡住了</div>
            <div className="text-slate-300 text-sm mb-5">长时间无响应（已判定为卡死）。你可以重开一局新牌局，当前进度将丢弃。</div>
            <button
              onClick={() => { setAiStuck(false); lastStuckSeqRef.current = null; stuckSinceRef.current = Date.now(); startGame(); }}
              className="px-6 py-2.5 bg-amber-500 hover:bg-amber-400 text-slate-900 font-semibold rounded-lg text-base"
            >
              重开新局
            </button>
          </div>
        </div>
      )}

      {/* ================= AI 超时本地兜底提醒 ================= */}
      {fallbackToast && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[60] px-5 py-2.5 bg-amber-500/95 text-slate-900 font-semibold rounded-xl shadow-2xl text-sm animate-pulse">
          ⚠️ AI 返回超时，本次出牌采用本地策略
        </div>
      )}

      {/* ================= 每日局数软提醒（本地不限制实际局数，仅展示） ================= */}
      {memberLoginEnabled && memberQuotaToast && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[60] px-5 py-2.5 bg-slate-700/95 text-white font-semibold rounded-xl shadow-2xl text-sm">
          今日 {MEMBER_LIMIT} 局已用完，仍可继续玩
        </div>
      )}

      {/* ================= 上方：对家 (Partner) ================= */}
      <div className="flex-none h-[20vh] sm:h-[22vh] flex flex-row justify-center items-end pb-4 sm:pb-5 relative px-1 sm:px-2">
        {/* 复盘模式：仅当轮到对家时显示手牌（左侧半段） */}
        {isReplayOpen && activeReplayPlayer === "PartnerBot" && displayHands?.PartnerBot && (
          <div className="hidden sm:flex items-end justify-end flex-1 max-w-[280px] lg:max-w-[400px] xl:max-w-[480px] mr-2 lg:mr-3">
            <ReplayHandStrip cards={displayHands.PartnerBot.slice(0, Math.ceil(displayHands.PartnerBot.length / 2))} variant="top" />
          </div>
        )}

        {/* 中央：头像和信息 */}
        <div className="flex flex-col items-center z-10 flex-shrink-0 relative mb-4 sm:mb-6">
          {/* 剩余牌数标签 - 移到头像上方，避免被出牌遮挡 */}
          <div className={`absolute -top-8 left-1/2 -translate-x-1/2 text-xs sm:text-sm px-2 sm:px-3 py-0.5 sm:py-1 rounded-full border font-bold shadow-lg whitespace-nowrap z-50 ${
            displayBotCounts.PartnerBot <= 6 
              ? 'text-red-200 bg-red-900/80 border-red-400/50 animate-pulse' 
              : 'text-blue-200 bg-blue-900/80 border-blue-400/50'
          }`}>
            剩余 {displayBotCounts.PartnerBot}张
          </div>
          
          {/* 得分标签 - 移到头像右侧 */}
          <div className="absolute top-1/2 -translate-y-1/2 left-full ml-2 sm:ml-3 text-xs sm:text-sm font-bold text-yellow-400 drop-shadow-md bg-black/60 px-2 py-1 rounded-lg border border-yellow-500/30 whitespace-nowrap">
            {totalScores.PartnerBot}分
          </div>
          
          <div className="relative">
            {/* AI思考动态光圈 */}
            {aiProcessing && activeAiPlayer === 'PartnerBot' && <div className="ai-thinking-ring"></div>}
            <div 
              onClick={() => handleAiAvatarClick('PartnerBot')}
              onPointerDown={() => onAvatarPointerDown('PartnerBot')}
              onPointerUp={onAvatarPointerUp}
              onPointerLeave={onAvatarPointerUp}
              className={`w-12 h-12 sm:w-14 sm:h-14 badge-circle badge-blue flex items-center justify-center text-base sm:text-lg font-extrabold text-white bg-cover bg-center ${gameState.turn === 'PartnerBot' && (Date.now() - turnStartTime > 3000) ? 'cursor-pointer' : 'cursor-help'}`} 
              style={{backgroundImage: `url(${SOUND_BASE}avatars/partner.png)`, backgroundSize: 'cover', backgroundPosition: 'center'}}
            >
            </div>
          </div>
        </div>
        
        {/* 复盘模式：仅当轮到对家时显示手牌（右侧半段） */}
        {isReplayOpen && activeReplayPlayer === "PartnerBot" && displayHands?.PartnerBot && (
          <div className="hidden sm:flex items-end justify-start flex-1 max-w-[280px] lg:max-w-[400px] xl:max-w-[480px] ml-2 lg:ml-3">
            <ReplayHandStrip cards={displayHands.PartnerBot.slice(Math.ceil(displayHands.PartnerBot.length / 2))} variant="top" />
          </div>
        )}

        {/* 出牌位（在头像下方更低位置，避免遮挡头像） */}
        <div className="absolute bottom-[-18vh] sm:bottom-[-18vh] left-1/2 -translate-x-1/2 flex flex-col items-center gap-1 sm:gap-2"
             style={{ zIndex: playedBubbleZ.PartnerBot }}>
          <PlayedCardBubble moves={displayRoundMoves["PartnerBot"]} layout="horizontal" />
          {/* 小屏时手牌显示在出牌位下方 */}
          {isReplayOpen && activeReplayPlayer === "PartnerBot" && displayHands?.PartnerBot && (
            <div className="sm:hidden">
              <ReplayHandStrip cards={displayHands.PartnerBot} variant="top" />
            </div>
          )}
        </div>
      </div>

      {/* ================= 中间：左右家 + 桌面信息 ================= */}
      <TableBoard {...tableProps} />

      {/* ================= 下方：我的区域 ================= */}
      <div className="flex-1 flex flex-col justify-end pb-0.5 sm:pb-1 relative bg-gradient-to-t from-black/70 to-transparent">
        {!isReplayOpen && gameResult && !gameResultVisible && (
          <div className="absolute top-3 sm:top-4 left-3 sm:left-8 z-30">
            <button
              onClick={() => setGameResultVisible(true)}
                className="px-4 py-1 rounded-full btn-primary text-xs sm:text-sm"
            >
              查看结算
            </button>
          </div>
        )}
        
        {/* --- 复盘手牌显示区域（在按钮上方）；仅当轮到 User 时显示 --- */}
        {isReplayOpen && activeReplayPlayer === "User" && displayHands?.User && (
          <div className="w-full flex flex-col items-center px-2 sm:px-4 mb-2 sm:mb-3 flex-shrink-0 gap-2">
            {/* 我的出牌位（显示在手牌前面） */}
            <div className="relative flex justify-center" style={{ zIndex: playedBubbleZ.User }}>
              <PlayedCardBubble moves={displayRoundMoves["User"]} layout="horizontal" />
            </div>
            {/* 我的手牌 */}
            <div className="w-full flex justify-center max-h-[30vh] sm:max-h-[25vh] overflow-y-auto">
              <ReplayHandStrip cards={displayHands.User} variant="bottom" />
            </div>
          </div>
        )}

        {/* 非复盘模式：我的出牌位（在手牌上方）
            我的出牌位必须留在根 stacking context（这样才能与左右家/上家出牌位直接比较 z，
            实现「谁最新出牌谁盖住别人」）；只有按钮 + 手牌包进 z-[20] 的独立 context，
            否则手牌 z=999/113 会盖住四个出牌位。 */}
        {!isReplayOpen && (
          <div className="relative w-full flex justify-center mb-3 sm:mb-8" style={{ zIndex: playedBubbleZ.User }}>
            <PlayedCardBubble moves={displayRoundMoves["User"]} layout="horizontal" />
          </div>
        )}

        {/* 操作按钮：独立 stacking context（z-[70] 高于四个出牌位 z≤60），
            防止 LeftBot/其他家出牌气泡(移动端下探)盖住「不出」按钮 */}
        <div className="relative z-[70]">
        {/* 操作按钮 */}
        <div className="relative flex justify-center gap-3 sm:gap-6 mb-1 sm:mb-2 z-20 px-2 flex-shrink-0">
          {isReplayOpen ? (
            <div className="flex flex-col items-center gap-2 bg-slate-900/70 border border-slate-700 rounded-xl sm:rounded-2xl px-3 sm:px-6 py-2 sm:py-3 shadow-inner text-xs sm:text-sm text-slate-200 w-full max-w-md sm:max-w-none">
              {replayLoading && (
                <div className="flex items-center gap-2 text-slate-300 text-xs sm:text-sm py-1">
                  <span className="inline-block w-4 h-4 border-2 border-slate-500 border-t-sky-400 rounded-full animate-spin" />
                  <span>正在加载复盘数据…</span>
                </div>
              )}
              {replayProgressElement}
              <div className={`grid gap-2 w-full sm:flex sm:flex-row sm:gap-3 sm:w-auto ${aiCoachEnabled ? 'grid-cols-2' : 'grid-cols-3'}`}>
                <button
                  onClick={toggleReplay}
                  disabled={replayLoading}
                  className="px-4 sm:px-4 py-2 sm:py-1 rounded-lg sm:rounded-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:hover:bg-blue-600 text-white text-sm sm:text-base font-semibold transition-colors"
                >
                  {replayLoading ? "加载中…" : (replayPlaying ? "⏸ 暂停" : "▶ 继续")}
                </button>
                <button
                  onClick={closeReplay}
                  className="px-4 sm:px-4 py-2 sm:py-1 rounded-lg sm:rounded-full bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm sm:text-base font-semibold transition-colors"
                >
                  ✕ 退出复盘
                </button>
                <button
                  onClick={openReplay}
                  className="px-4 sm:px-4 py-2 sm:py-1 rounded-lg sm:rounded-full bg-amber-500 hover:bg-amber-400 text-black text-sm sm:text-base font-semibold transition-colors"
                >
                  🔄 重新加载
                </button>
                {aiCoachEnabled && (
                  <button
                    onClick={_openCoach}
                    disabled={coachLoading}
                    className="px-4 sm:px-4 py-2 sm:py-1 rounded-lg sm:rounded-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:hover:bg-emerald-600 text-white text-sm sm:text-base font-semibold transition-colors"
                  >
                    {coachLoading ? "分析中…" : "🤖 AI 教练"}
                  </button>
                )}
              </div>
            </div>
          ) : (
            (!gameId ? (
              <div className="flex items-center gap-3 sm:gap-4 sm:translate-y-6">
                <button onClick={startGame} className="px-8 sm:px-12 py-3 rounded-full text-base sm:text-lg font-bold btn-primary transform transition-all duration-150 active:scale-95 hover:scale-105">
                  开始游戏
                </button>
                <button
                  onClick={() => setRulesOpen(true)}
                  className="px-5 sm:px-7 py-3 rounded-full text-base sm:text-lg font-semibold btn-ghost border border-slate-500/50 text-slate-200 transform transition-all duration-150 active:scale-95 hover:scale-105"
                >
                  查看规则
                </button>
              </div>
            ) : (
              <>
                <button
                  onClick={passTurn}
                  disabled={isActionPending}
                  className={`px-5 sm:px-8 py-1.5 sm:py-2 rounded-xl font-bold btn-ghost transform transition-all duration-150 ${isActionPending ? 'opacity-50 cursor-not-allowed' : 'active:scale-90 hover:scale-105 active:shadow-inner'} relative overflow-hidden group ${passShake ? 'pass-shake' : ''}`}
                  style={(gameState.state === 'playing' && gameState.turn === 'User')
                    ? { background: 'rgba(15, 23, 42, 0.92)', borderColor: 'rgba(255, 255, 255, 0.28)', boxShadow: '0 4px 14px rgba(0, 0, 0, 0.35)' }
                    : undefined}
                  onTouchStart={(e) => {
                    if (!isActionPending) e.currentTarget.classList.add('scale-90');
                  }}
                  onTouchEnd={(e) => {
                    e.currentTarget.classList.remove('scale-90');
                  }}
                >
                  <span className="relative z-10">{isActionPending ? "请稍候..." : "不出"}</span>
                  <span className="absolute inset-0 bg-white opacity-0 group-active:opacity-20 transition-opacity duration-150"></span>
                </button>
                <button 
                  onClick={playCards} 
                  disabled={isActionPending || selected.length===0} 
                  className={`px-6 sm:px-10 py-1.5 sm:py-2 rounded-xl font-bold btn-primary transform transition-all duration-150 relative overflow-hidden group ${
                    (isActionPending || selected.length === 0) 
                      ? 'cursor-not-allowed opacity-50' 
                      : 'active:scale-90 hover:scale-105 active:shadow-lg'
                  }`}
                  onTouchStart={(e) => {
                    if (!isActionPending && selected.length > 0) {
                      e.currentTarget.classList.add('scale-90');
                    }
                  }}
                  onTouchEnd={(e) => {
                    e.currentTarget.classList.remove('scale-90');
                  }}
                >
                  <span className="relative z-10">{isActionPending ? "处理中..." : "出牌"}</span>
                  {!isActionPending && selected.length > 0 && (
                    <span className="absolute inset-0 bg-white opacity-0 group-active:opacity-30 transition-opacity duration-150"></span>
                  )}
                </button>
              </>
            ))
          )}

        </div>
        <ScorePanel {...scorePanelProps} />
        </div>

        {/* 手牌区：包进独立 stacking context，保持低于四个出牌位（按钮已在 z-[70] 之上）
            mb-2 让手牌与屏幕底边留一点呼吸空间；flex-shrink-0 防止视口变矮时
            手牌区被压缩；卡片实际尺寸由 HandCards 内按可用高度缩放保证不溢出 */}
        <div className="relative z-[20] mb-2 sm:mb-3 flex-shrink-0">
        {/* --- 手牌区域 (修复居中问题) --- */}
          <HandCards
            cards={displayedMyHand}
            selected={selected}
            handDisplayParams={handDisplayParams}
            isReplayOpen={isReplayOpen}
            onToggleCard={toggleCard}
            onReorder={handleReorder}
            cardHitRefs={cardHitRefsRef}
            registerCardHit={setCardHitRef}
          />
          {/* 底部信息 */}
          {!isReplayOpen && (
            <div className="text-center text-slate-500 text-[10px] sm:text-xs mt-0 px-2 break-words">
               已选: {selected.join(" ")}
            </div>
          )}
          </div>
      </div>

      {/* 炸弹爆炸特效 */}
      {bombTrigger && (
        <div className="bomb-container pointer-events-none" key={bombTrigger}>
           <div className="screen-shake">
              <div className="bomb-emoji" onAnimationEnd={() => setBombTrigger(null)}>💥</div>
           </div>
        </div>
      )}

      {/* 游戏结束结算弹窗 (移至最下方以确保 z-index 在 DOM 顺序中也是最高) */}
      <ResultModal
        gameResult={gameResult}
        gameResultVisible={gameResultVisible}
        gameId={gameId}
        lastCompletedGameId={lastCompletedGameId}
        setGameResultVisible={setGameResultVisible}
        openReplay={openReplay}
        setGameResult={setGameResult}
        setGameId={setGameId}
        setMyHand={setMyHand}
        resetMoveTracking={resetMoveTracking}
        closeReplay={closeReplay}
        setLastCompletedGameId={setLastCompletedGameId}
        setStatusMsg={setStatusMsg}
        gameOverVoiceTimerRef={gameOverVoiceTimerRef}
        resultShownForGameRef={resultShownForGameRef}
        setSelected={setSelected}
      />

      {/* v3 会员注册/登录弹窗（毛玻璃，放在 ResultModal 之后确保 z-index 最高） */}
      {memberLoginEnabled && (
      <AuthModal
        open={authModalOpen}
        onClose={() => setAuthModalOpen(null)}
        onSuccess={(user) => {
          applyAuthSession(user.token, user);
          setAuthModalOpen(null);
          // 登录后立即刷新会员今日局数
          fetchMe(user.token).then((info) => {
            if (info) setAuthUser({ nickname: info.nickname, email: info.email, plays_today: info.plays_today, limit: info.limit });
          });
          setStatusMsg(`欢迎回来，${user.nickname}！`);
        }}
        onLogout={() => { logoutUser(); setAuthModalOpen(null); }}
        authUser={authUser}
        initialMode={authModalMode}
      />
      )}

      {/* 开始页「查看规则」弹窗 */}
      <RulesModal open={rulesOpen} onClose={() => setRulesOpen(false)} />

      {/* AI 教练复盘弹窗（放在最末尾，与其它全屏弹窗 z-index 同级最高层） */}
      {aiCoachEnabled && (
      <CoachModal
        open={coachOpen}
        loading={coachLoading}
        error={coachError}
        reviews={coachReviews}
        message={coachMessage}
        cached={coachCached}
        onClose={() => setCoachOpen(false)}
      />
      )}

    </div>
  );
}

export default App;