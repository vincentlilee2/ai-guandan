// 3.1/路线2 试点：ScorePanel —— 我的得分/登录翻转按钮面板，纯展示组件。
// 机械搬运自 App.jsx:2225-2282，零行为变更；props 透传原 state/handler。
// 说明：totalScores/isLoggedIn/userName/scoreFlipFace 是 App 主逻辑(结算/分数同步/登录)与展示共用的
// 全局会话状态，故此处保持纯展示 + props 透传（而非组件自治 hook），避免破坏跨组件共享。
import React from 'react'

export function ScorePanel(props) {
  const {
    isReplayOpen,
    handleScoreCardClick,
    scoreFlipFace,
    totalScores,
    isLoggedIn,
    userName,
    startPage,
    memberLoginEnabled = false,
  } = props;

  // 会员登录未开通：纯得分卡，无登录翻转面、不可点击
  if (!memberLoginEnabled) {
    return (
      <>
        {!isReplayOpen && (
          <div className={`absolute right-4 sm:right-10 ${startPage ? '-top-12' : 'top-1/2'} -translate-y-1/2 z-30`}>
            <button type="button" className="relative w-[78px] h-[40px] sm:w-[96px] sm:h-[50px] focus:outline-none cursor-default">
              <div className="relative w-full h-full rounded-full border border-yellow-400/60 flex flex-col items-center justify-center bg-black/60">
                <div className="absolute inset-0 rounded-full bg-gradient-to-br from-yellow-500/80 to-amber-600/80 shadow-[0_0_12px_rgba(250,204,21,0.35)]" style={{ opacity: 0.25 }}></div>
                <div className="relative z-10 text-[10px] sm:text-xs text-white drop-shadow-[0_0_6px_rgba(255,255,255,0.9)]">我的得分</div>
                <div className="relative z-10 text-sm sm:text-base font-bold text-white drop-shadow-[0_0_10px_rgba(255,255,255,0.95)]">
                  {totalScores.User}
                </div>
              </div>
            </button>
          </div>
        )}
      </>
    );
  }

  return (
    <>
      {/* 我的得分 / 登录翻转按钮：与按钮同一行靠右显示 */}
      {!isReplayOpen && (
        <div className={`absolute right-4 sm:right-10 ${startPage ? '-top-12' : 'top-1/2'} -translate-y-1/2 z-30`}>
          <button
            type="button"
            onClick={handleScoreCardClick}
            className="relative w-[78px] h-[40px] sm:w-[96px] sm:h-[50px] focus:outline-none"
            style={{ perspective: "800px", WebkitPerspective: "800px" }}
          >
            <div
              className="relative w-full h-full transition-transform duration-700"
              style={{
                transformStyle: "preserve-3d",
                WebkitTransformStyle: "preserve-3d",
                transform: scoreFlipFace === "login" ? "rotateY(180deg)" : "rotateY(0deg)",
                WebkitTransform: scoreFlipFace === "login" ? "rotateY(180deg)" : "rotateY(0deg)",
                willChange: "transform"
              }}
            >
              <div
                className="absolute inset-0 rounded-full border border-yellow-400/60 flex flex-col items-center justify-center bg-black/60"
                style={{
                  backfaceVisibility: "hidden",
                  WebkitBackfaceVisibility: "hidden",
                  transform: "translateZ(1px)",
                  WebkitTransform: "translateZ(1px)"
                }}
              >
                <div
                  className="absolute inset-0 rounded-full bg-gradient-to-br from-yellow-500/80 to-amber-600/80 shadow-[0_0_12px_rgba(250,204,21,0.35)]"
                  style={{ opacity: 0.25 }}
                ></div>
                <div className="relative z-10 text-[10px] sm:text-xs text-white drop-shadow-[0_0_6px_rgba(255,255,255,0.9)]">我的得分</div>
                <div className="relative z-10 text-sm sm:text-base font-bold text-white drop-shadow-[0_0_10px_rgba(255,255,255,0.95)]">
                  {totalScores.User}
                </div>
              </div>
              <div
                className="absolute inset-0 rounded-full border border-emerald-300/60 flex items-center justify-center bg-black/60"
                style={{
                  backfaceVisibility: "hidden",
                  WebkitBackfaceVisibility: "hidden",
                  transform: "rotateY(180deg) translateZ(1px)",
                  WebkitTransform: "rotateY(180deg) translateZ(1px)"
                }}
              >
                <div
                  className="absolute inset-0 rounded-full bg-gradient-to-br from-emerald-500/80 to-teal-600/80 shadow-[0_0_12px_rgba(16,185,129,0.35)]"
                  style={{ opacity: 0.75 }}
                ></div>
                <div className="relative z-10 text-sm sm:text-base font-bold text-white tracking-widest drop-shadow-[0_0_10px_rgba(255,255,255,0.95)]">
                  {isLoggedIn ? (userName || "账号") : "登录"}
                </div>
              </div>
            </div>
          </button>
        </div>
      )}
    </>
  );
}