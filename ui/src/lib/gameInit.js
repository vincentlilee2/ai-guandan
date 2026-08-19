// 3.1 拆分：游戏初始化常量与纯 helper（从 App.jsx 抽出，便于 useGameState 收口时引用）
// 本文件为模块级纯定义：仅依赖 window / import.meta.env，无 React 依赖，可被 App 与 hooks 共享。

export const SOUND_BASE = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "/");
export const PLAY_SFX = `${SOUND_BASE}sounds/common/play_card.wav`; // 短促出牌音效，紧接语音播报
export const BOMB_SFX = `${SOUND_BASE}sounds/common/bomb.mp3`; // 炸弹爆炸音效
export const WIN_SFX = `${SOUND_BASE}sounds/sfx/sfx_cheer.mp3`; // [新增] 胜利欢呼音效
export const GAME_OVER_SFX = `${SOUND_BASE}sounds/sfx/sfx_gameover.mp3`;
export const ERROR_SFX = `${SOUND_BASE}sounds/sfx/sfx_error_short.mp3`; // 错误短音效
export const ERROR_VOICE_BASE = `${SOUND_BASE}sounds/voice/error/`;
export const SHOW_RESULT_AFTER_FLUSH_MS = 600; // 小屏/移动端需要更长时间完成渲染
export const MIN_RESULT_AFTER_LAST_RENDER_MS = 1200; // 至少让最后一手在桌面上可见一会儿再弹结算
export const FINISH_SETTLE_MS = 2500; // 进入 finished 后至少等待一段时间，给移动端 1-2 次轮询窗口补齐最后几手

// 使用 import.meta.glob 获取所有获胜音效
// 注意：需要确保目录 `sounds/sfx/` 下有对应文件：
// win_User.mp3, win_LeftBot.mp3, win_RightBot.mp3, win_PartnerBot.mp3
// 以及 announce_touyou.mp3, announce_eryou.mp3, announce_sanyou.mp3, announce_score.mp3
// (如果部分缺失，loadAudio 会 fail gracefully)

export const createEmptyRoundMoves = () => ({
  User: [],
  RightBot: [],
  PartnerBot: [],
  LeftBot: []
});

export const PLAYER_LABELS = {
  User: "我方 (User)",
  RightBot: "右家 (RightBot)",
  PartnerBot: "对家 (PartnerBot)",
  LeftBot: "左家 (LeftBot)"
};

export const DEFAULT_TOTAL_SCORES = { User: 0, RightBot: 0, PartnerBot: 0, LeftBot: 0 };
export const ANON_ID_KEY = "game:anon_id";
export const LOCAL_SCORE_KEY = "game:local_total_scores";

export const getAnonId = () => {
  try {
    let id = window.localStorage.getItem(ANON_ID_KEY);
    if (!id) {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        id = window.crypto.randomUUID();
      } else {
        id = `anon_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
      }
      window.localStorage.setItem(ANON_ID_KEY, id);
    }
    return id;
  } catch (_) {
    return `anon_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  }
};

export const loadLocalScores = () => {
  try {
    const raw = window.localStorage.getItem(LOCAL_SCORE_KEY);
    if (!raw) return { ...DEFAULT_TOTAL_SCORES };
    const parsed = JSON.parse(raw);
    return {
      User: Number(parsed?.User || 0),
      RightBot: Number(parsed?.RightBot || 0),
      PartnerBot: Number(parsed?.PartnerBot || 0),
      LeftBot: Number(parsed?.LeftBot || 0)
    };
  } catch (_) {
    return { ...DEFAULT_TOTAL_SCORES };
  }
};

export const saveLocalScores = (scores) => {
  try {
    const payload = {
      User: Number(scores?.User || 0),
      RightBot: Number(scores?.RightBot || 0),
      PartnerBot: Number(scores?.PartnerBot || 0),
      LeftBot: Number(scores?.LeftBot || 0)
    };
    window.localStorage.setItem(LOCAL_SCORE_KEY, JSON.stringify(payload));
  } catch (_) {
    // ignore
  }
};

export const mergeScores = (base, delta) => ({
  User: Number(base?.User || 0) + Number(delta?.User || 0),
  RightBot: Number(base?.RightBot || 0) + Number(delta?.RightBot || 0),
  PartnerBot: Number(base?.PartnerBot || 0) + Number(delta?.PartnerBot || 0),
  LeftBot: Number(base?.LeftBot || 0) + Number(delta?.LeftBot || 0)
});

// 复盘轮转优化：给定当前 history 下标，计算「下一次应停靠」的下标。
// 玩法是每手依次展示，但轮次结束时（某家出牌 + 其余三家 PASS + ROUND_END）会重复展示
// 赢家刚出过的牌，显得拖沓。这里把轮末尾巴折叠掉：
//   - 一串 PASS 若以 ROUND_END 收尾（= 前一 PLAY 是轮末赢家手）→ 整段折叠到下一轮首发；
//   - 中途的 PASS（后面还有人出牌）照常展示；
//   - ROUND_END 是纯轮次标记，直接跳过。
// 返回的 index 恒为合法下标（0..len-1），供 advanceReplay / 进度跳转复用。
export const replaySkipTarget = (history, index) => {
  const len = Array.isArray(history) ? history.length : 0;
  if (len === 0) return -1;
  const clamp = (i) => Math.max(0, Math.min(i, len - 1));
  let i = clamp(index);
  const m = history[i];
  if (!m) return i;

  if (m.action === "ROUND_END") {
    i += 1;
    while (i < len && history[i] && (history[i].action === "ROUND_END" || history[i].action === "PASS")) i++;
    return i >= len ? len - 1 : i;
  }

  if (m.action === "PASS") {
    // 这串 PASS 是否以 ROUND_END 收尾（轮末尾巴）？
    let j = i;
    while (j < len && history[j] && history[j].action === "PASS") j++;
    if (j < len && history[j] && history[j].action === "ROUND_END") {
      let k = j + 1;
      while (k < len && history[k] && (history[k].action === "ROUND_END" || history[k].action === "PASS")) k++;
      return k >= len ? len - 1 : k;
    }
    return i; // 中途 PASS，照常展示
  }

  return i; // PLAY
};

// 复盘「上一轮」跳转：在 history 中找 index 之前最近一个 PLAY 下标。
// 返回下标或 -1（已到开头）。供进度条「上一轮」按钮使用。
export const replayPrevTarget = (history, index) => {
  const len = Array.isArray(history) ? history.length : 0;
  if (len === 0) return -1;
  for (let i = index - 1; i >= 0; i--) {
    const m = history[i];
    if (m && m.action === "PLAY") return i;
  }
  return -1;
};
