// 复盘 AI 教练弹窗：展示 User 每手出牌的教练点评（situation / mistake / advice）。
// 纯展示组件，仅依赖 props；loading/error/空/已缓存各态都在这里渲染。
import React from 'react'

const CoachModal = ({ open, loading, error, reviews, message, cached, onClose }) => {
  if (!open) return null;

  const hasReviews = Array.isArray(reviews) && reviews.length > 0;

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/80 backdrop-blur-md p-2 sm:p-4 pointer-events-auto"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="bg-slate-800 border-2 border-emerald-500 rounded-xl sm:rounded-2xl p-4 sm:p-8 max-w-3xl w-full max-h-[90vh] overflow-y-auto pointer-events-auto shadow-[0_0_60px_rgba(16,185,129,0.35)]">
        <h2 className="text-xl sm:text-2xl font-bold text-emerald-400 mb-2">🎓 AI 教练复盘</h2>
        {cached && <div className="text-xs text-gray-400 mb-2">（已缓存结果）</div>}

        {loading && (
          <div className="text-sm sm:text-base text-gray-300 py-6 text-center">教练分析中，请稍候…</div>
        )}

        {!loading && error && (
          <div className="text-sm sm:text-base text-red-400 py-4 bg-red-900/20 border border-red-700/50 rounded-lg p-3">
            {error}
          </div>
        )}

        {!loading && !error && Array.isArray(reviews) && reviews.length === 0 && (
          <div className="text-sm sm:text-base text-emerald-300 py-6 text-center">
            {message || "本局没有 User 出牌记录。"}
          </div>
        )}

        {!loading && !error && hasReviews && reviews.map((r, i) => (
          <div key={i} className="bg-slate-700/40 rounded-lg p-3 mb-3 border border-slate-600/50">
            <div className="text-sm text-gray-200 mb-1">
              <span className="text-emerald-400 font-bold mr-2">
                {r.action === "PASS" ? "PASS" : `出牌：${r.desc}`}
              </span>
            </div>
            {r.situation && <div className="text-xs sm:text-sm text-gray-300 mb-2">📋 {r.situation}</div>}
            {r.mistake && <div className="text-xs sm:text-sm text-yellow-300 mb-2">❌ {r.mistake}</div>}
            {r.advice && <div className="text-xs sm:text-sm text-blue-300">💡 {r.advice}</div>}
          </div>
        ))}

        <button
          onClick={onClose}
          className="w-full py-2.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-white font-semibold transition-colors"
        >
          关闭
        </button>
      </div>
    </div>
  );
};

export default CoachModal
