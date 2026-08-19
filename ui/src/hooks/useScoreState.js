// 3.1/A 拆分：细粒度 hook（由 useGameState 大桶按域分组而来）。
// 机械搬运、零行为变更：原 useGameState 的声明按归属域拆分到以下 hook，App 合并调用等价。
import { useState, useRef } from 'react'
import { DEFAULT_TOTAL_SCORES } from '../lib/gameInit'

export function useScoreState() {
  const [totalScores, setTotalScores] = useState({ ...DEFAULT_TOTAL_SCORES });
  const [gameResult, setGameResult] = useState(null);
  const [gameResultVisible, _setGameResultVisible] = useState(false);
  const gameResultVisibleRef = useRef(false);
  const resultShownForGameRef = useRef(null);
  const pendingResultForGameRef = useRef(null);
  const localScoreAppliedForGameRef = useRef(new Set());
  const [scoreFlipFace, setScoreFlipFace] = useState("score");

  return {
    totalScores,
    setTotalScores,
    gameResult,
    setGameResult,
    gameResultVisible,
    _setGameResultVisible,
    gameResultVisibleRef,
    resultShownForGameRef,
    pendingResultForGameRef,
    localScoreAppliedForGameRef,
    scoreFlipFace,
    setScoreFlipFace,
  };
}
