// 3.1/A 拆分：细粒度 hook（由 useGameState 大桶按域分组而来）。
// 机械搬运、零行为变更：原 useGameState 的声明按归属域拆分到以下 hook，App 合并调用等价。
import { useState, useRef } from 'react'
import { createEmptyRoundMoves } from '../lib/gameInit'

export function useTableState() {
  const [turnStartTime, setTurnStartTime] = useState(0);
  const [roundMoves, setRoundMoves] = useState(() => createEmptyRoundMoves());
  const [aiProcessing, setAiProcessing] = useState(false);
  const [activeAiPlayer, setActiveAiPlayer] = useState("");
  const [statusMsg, setStatusMsg] = useState("欢迎来到掼蛋 AI 对战！");
  const [isReplayOpen, setIsReplayOpen] = useState(false);
  const isReplayOpenRef = useRef(false);
  const [replayData, setReplayData] = useState(null);
  const [replayIndex, setReplayIndex] = useState(-1);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replayError, setReplayError] = useState(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const finishDetectedAtRef = useRef(0);
  const finishDetectedForGameRef = useRef(null);
  const aiMoveQueueRef = useRef([]);
  const winCelebrationPlayedRef = useRef(new Set());
  const audioUnlockedRef = useRef(false);
  const audioPoolRef = useRef(new Map()); // 预加载的音频对象池
  const winVoiceTimerRef = useRef(null);
  const gameOverVoiceTimerRef = useRef(null);
  const voiceQueueRef = useRef([]); // 待播放语音队列
  const isPlayingVoiceRef = useRef(false); // 是否正在播放语音
  const currentAudioRef = useRef(null); // 当前正在播放的Audio对象
  const pendingWinCelebrationRef = useRef(new Set());
  const gameOverSummaryPlayedRef = useRef(false);
  const aiQueueTimerRef = useRef(null);
  const aiProcessingRef = useRef(false);
    // eslint-disable-next-line react-hooks/purity
  const lastAiDisplayTimeRef = useRef(Date.now());
  const replayTimerRef = useRef(null);
  const longPressTimerRef = useRef(null); // [Add] 用于头像长按纠错

  return {
    turnStartTime,
    setTurnStartTime,
    roundMoves,
    setRoundMoves,
    aiProcessing,
    setAiProcessing,
    activeAiPlayer,
    setActiveAiPlayer,
    statusMsg,
    setStatusMsg,
    isReplayOpen,
    setIsReplayOpen,
    isReplayOpenRef,
    replayData,
    setReplayData,
    replayIndex,
    setReplayIndex,
    replayPlaying,
    setReplayPlaying,
    replayError,
    setReplayError,
    replayLoading,
    setReplayLoading,
    finishDetectedAtRef,
    finishDetectedForGameRef,
    aiMoveQueueRef,
    winCelebrationPlayedRef,
    audioUnlockedRef,
    audioPoolRef,
    winVoiceTimerRef,
    gameOverVoiceTimerRef,
    voiceQueueRef,
    isPlayingVoiceRef,
    currentAudioRef,
    pendingWinCelebrationRef,
    gameOverSummaryPlayedRef,
    aiQueueTimerRef,
    aiProcessingRef,
    lastAiDisplayTimeRef,
    replayTimerRef,
    longPressTimerRef,
  };
}
