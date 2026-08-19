// 3.1/A 拆分：细粒度 hook（由 useGameState 大桶按域分组而来）。
// 机械搬运、零行为变更：原 useGameState 的声明按归属域拆分到以下 hook，App 合并调用等价。
import { useState, useRef } from 'react'

export function useHandState() {
  const [handDisplayParams, setHandDisplayParams] = useState(() => {
    const width = window.innerWidth;
    // 根据屏幕宽度动态计算参数
    let base, overlap, paddingLeft;
    if (width <= 360) {
      // 小屏（如Galaxy Fold）
      base = 0.60; overlap = '-62px'; paddingLeft = '24px';
    } else if (width <= 390) {
      // iPhone 14等
      base = 0.65; overlap = '-58px'; paddingLeft = '28px';
    } else if (width <= 414) {
      // iPhone 14 Pro Max等
      base = 0.68; overlap = '-54px'; paddingLeft = '30px';
    } else if (width <= 430) {
      // iPhone 16 Plus等
      base = 0.67; overlap = '-57px'; paddingLeft = '28px';
    } else {
      // 超大屏
      base = 0.72; overlap = '-50px'; paddingLeft = '32px';
    }
    return { scale: base, overlap, paddingLeft };
  });
  const [myHand, setMyHand] = useState([]);
  // 用户手动整理后的展示顺序（扁平 id 数组）；仅本局会话内保留，重发/新局重置。
  // 只影响展示，出牌逻辑仍以 myHand（服务器权威）为准。
  const [handOrder, setHandOrder] = useState([]);
  const [selected, setSelected] = useState([]);
  const [passShake, setPassShake] = useState(false);
  const [bombTrigger, setBombTrigger] = useState(null);
  const processedHistoryCountRef = useRef(new Map());
  const lastProcessedHistoryIdRef = useRef(-1);
  const optimisticPendingCountRef = useRef(new Map());
  const lastMoveRenderedAtRef = useRef(0);
  const hasReceivedHandRef = useRef(false);
  const lastUserPlayTimeRef = useRef(0);
  const pendingPlayCardsCountRef = useRef(0);
  const cardHitRefsRef = useRef(new Map());
  const _skipHistoryReplayRef = useRef(false);
  const [isActionPending, setIsActionPending] = useState(false);

  return {
    handDisplayParams,
    setHandDisplayParams,
    myHand,
    setMyHand,
    handOrder,
    setHandOrder,
    selected,
    setSelected,
    passShake,
    setPassShake,
    bombTrigger,
    setBombTrigger,
    processedHistoryCountRef,
    lastProcessedHistoryIdRef,
    optimisticPendingCountRef,
    lastMoveRenderedAtRef,
    hasReceivedHandRef,
    lastUserPlayTimeRef,
    pendingPlayCardsCountRef,
    cardHitRefsRef,
    _skipHistoryReplayRef,
    isActionPending,
    setIsActionPending,
  };
}
