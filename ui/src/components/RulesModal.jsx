// 规则查看弹窗：展示 system prompt 中的计分规则与比牌规则（永远打2 的简化玩法）。
// 纯展示组件，无网络请求；规则文本与 backend/tactics_data.json system_prompts.static_rules 保持一致。
import React from 'react'

const DISCLAIMER = "本项目按掼蛋扑克简化玩法（永远打2）与AI玩家对战并复盘，仅可用于个人学习AI应用与辅导提高掼蛋技能。";

// 与 backend/tactics_data.json 的 static_rules 同源的规则内容（前端展示版）
const RULES = [
  {
    title: "📊 计分规则",
    items: [
      { k: "双游（队友包揽前两名）", v: "基准分 300" },
      { k: "一三游（队友分获第一和第三）", v: "基准分 200" },
      { k: "单游（队友分获第一和第四）", v: "基准分 100" },
      { k: "炸弹翻倍", v: "6张 ×2，7张 ×4，8张及天王炸 ×8" },
    ],
  },
  {
    title: "🃏 比牌规则",
    items: [
      { k: "普通牌型", v: "单张、对子、三张、顺子、连对、钢板：只能“大管小”，且必须牌型一致" },
      { k: "大小比较", v: "大王 > 小王 > 2 > A > K > Q > J > 10 > 9 > 8 > 7 > 6 > 5 > 4 > 3；对子、三张及三带二依此类推，三带二只比较三张的大小" },
      { k: "牌型提示", v: "本玩法中不存在“三带一”，只有“三带二”" },
      { k: "顺子", v: "5张连续单牌。特殊顺子：A-2-3-4-5（最小），2-3-4-5-6（次小）" },
      { k: "连对", v: "3对连续的对子（如 33 44 55）。特殊连对：AA 22 33（同牌型最小），22 33 44（同牌型次小）" },
      { k: "三带二", v: "三张带对子（如 333+44）。特殊三带二：AAA+33（同牌型次大），222+33（同牌型最大），只按三张大小比较" },
      { k: "钢板（两个连续三张）", v: "属于普通牌型，不是炸弹！会被任何炸弹（4张及以上）管死" },
      { k: "炸弹", v: "4张及以上的炸弹 > 所有普通牌型（含钢板）。炸弹之间按张数和点数比大小" },
      { k: "红桃2（逢人配）", v: "可以配任何牌型（但不能与王配对）" },
    ],
  },
];

const RulesModal = ({ open, onClose }) => {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/70 backdrop-blur-md p-3 sm:p-4"
      onClick={onClose}
    >
      <div
        className="bg-slate-800 border border-slate-600 rounded-xl sm:rounded-2xl p-4 sm:p-6 max-w-lg sm:max-w-2xl w-full max-h-[85vh] overflow-y-auto shadow-2xl rules-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg sm:text-xl font-bold text-white">📖 玩法规则</h2>
          <button
            onClick={onClose}
            aria-label="关闭弹窗"
            className="w-8 h-8 flex items-center justify-center rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 text-lg font-bold transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="bg-amber-500/15 border border-amber-400/40 rounded-lg p-3 mb-4 text-xs sm:text-sm text-amber-200 leading-relaxed">
          {DISCLAIMER}
        </div>

        {RULES.map((section) => (
          <div key={section.title} className="mb-4">
            <h3 className="text-sm sm:text-base font-bold text-emerald-400 mb-2">{section.title}</h3>
            <div className="space-y-1.5">
              {section.items.map((item) => (
                <div key={item.k} className="flex flex-col sm:flex-row sm:items-baseline gap-0.5 sm:gap-2 text-xs sm:text-sm">
                  <span className="text-slate-300 shrink-0">{item.k}</span>
                  <span className="text-slate-400 sm:ml-auto sm:text-right sm:min-w-0 sm:break-words">：{item.v}</span>
                </div>
              ))}
            </div>
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

export default RulesModal;
