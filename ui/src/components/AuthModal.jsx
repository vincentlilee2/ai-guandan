// 会员注册/登录弹窗（Apple 毛玻璃半透明风格，适配移动端底部抽屉 + 输入不缩放）
// 三个 tab：登录 / 注册 / 账号（登录后展示昵称邮箱与今日局数、退出登录）
import React, { useState } from 'react'
import { MEMBER_LIMIT } from '../lib/quota'

const AuthModal = ({
  open,           // null | "login" | "account"
  onClose,        // () => void
  onSuccess,      // (user: {token,nickname,email}) => void
  onLogout,       // () => void
  authUser,       // {nickname,email,plays_today,limit} | null
  initialMode,    // "login" | "register"
}) => {
  const [mode, setMode] = useState(initialMode === "register" ? "register" : "login");
  const [nickname, setNickname] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const isAccount = open === "account" || authUser;
  const switchMode = (m) => { setMode(m); setError(""); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setError("");
    if (busy) return;

    // 前端轻校验（后端仍会兜底）
    if (mode === "register" && (!nickname.trim() || nickname.trim().length < 2 || /\s/.test(nickname))) {
      setError("昵称需为 2-20 个字符且不含空格");
      return;
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError("请输入正确的邮箱地址");
      return;
    }
    if (password.length < 6) {
      setError("密码至少需要 6 位");
      return;
    }

    setBusy(true);
    try {
      const url = mode === "register" ? "/api/auth/register" : "/api/auth/login";
      const body = mode === "register"
        ? { nickname: nickname.trim(), email: email.trim(), password }
        : { email: email.trim(), password };
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || (mode === "register" ? "注册失败，请重试" : "登录失败，请重试"));
        return;
      }
      // 成功 → 交回 App 更新登录态并关闭
      onSuccess({ token: data.token, nickname: data.nickname, email: data.email });
    } catch (err) {
      console.error("[AuthModal] 请求失败:", err);
      setError("网络异常，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full px-4 py-3 rounded-xl bg-white/60 border border-white/60 text-slate-900 placeholder-slate-400 text-base outline-none focus:bg-white/80 focus:border-white/90 focus:ring-2 focus:ring-blue-300/60 transition";

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-end sm:items-center justify-center bg-black/30 backdrop-blur-md p-0 sm:p-4"
      onClick={onClose}
    >
      <div
        className="bg-white/70 backdrop-blur-2xl rounded-t-3xl sm:rounded-3xl border border-white/50 shadow-2xl w-full max-w-md pb-[env(safe-area-inset-bottom)] sm:pb-6 px-5 sm:px-8 pt-3 sm:pt-6 overflow-y-auto max-h-[85vh] auth-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 移动端顶部拖拽条 */}
        <div className="sm:hidden w-10 h-1.5 rounded-full bg-slate-400/50 mx-auto mb-3" />

        {isAccount && authUser ? (
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-3 rounded-full bg-gradient-to-br from-blue-400 to-indigo-500 flex items-center justify-center text-2xl font-bold text-white shadow-lg">
              {(authUser.nickname || "会").slice(0, 1)}
            </div>
            <h2 className="text-xl font-bold text-slate-900 mb-1">{authUser.nickname}</h2>
            <div className="text-sm text-slate-500 mb-5 break-all">{authUser.email}</div>

            <div className="bg-white/60 rounded-2xl p-4 mb-6 border border-white/60">
              <div className="flex justify-between items-center mb-1">
                <span className="text-sm text-slate-600">今日已玩</span>
                <span className="text-lg font-bold text-slate-900">
                  {authUser.plays_today ?? 0}<span className="text-slate-400 text-sm font-normal"> / {authUser.limit ?? MEMBER_LIMIT} 局</span>
                </span>
              </div>
              <div className="h-2 rounded-full bg-slate-200/80 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-blue-400 to-indigo-500 transition-all"
                  style={{ width: `${Math.min(100, ((authUser.plays_today ?? 0) / (authUser.limit ?? MEMBER_LIMIT)) * 100)}%` }}
                />
              </div>
              {(authUser.plays_today ?? 0) >= (authUser.limit ?? MEMBER_LIMIT) ? (
                <div className="mt-3 text-sm text-amber-600 font-medium">今日额度已用完，仍可继续玩（不限局数）</div>
              ) : (
                <div className="mt-3 text-xs text-slate-500">会员每日可玩 {authUser.limit ?? MEMBER_LIMIT} 局，本地不限局数，该数字为服务器记录</div>
              )}
            </div>

            <button
              onClick={(e) => { e.stopPropagation(); onLogout(); }}
              className="w-full py-3 rounded-xl bg-slate-900/80 hover:bg-slate-900 text-white font-semibold text-base transition active:scale-[0.98]"
            >
              退出登录
            </button>
          </div>
        ) : (
          <>
            {/* Tab 切换 */}
            <div className="flex bg-white/50 rounded-2xl p-1 mb-5 border border-white/60">
              {["login", "register"].map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => switchMode(m)}
                  className={`flex-1 py-2.5 rounded-xl text-sm font-semibold transition ${
                    mode === m ? "bg-white text-slate-900 shadow" : "text-slate-500"
                  }`}
                >
                  {m === "login" ? "登录" : "注册"}
                </button>
              ))}
            </div>

            <form onSubmit={handleSubmit} noValidate>
              {mode === "register" && (
                <div className="mb-3">
                  <label className="block text-xs font-medium text-slate-500 mb-1.5">昵称</label>
                  <input
                    type="text"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="2-20 个字符"
                    autoComplete="nickname"
                    className={inputCls}
                  />
                </div>
              )}
              <div className="mb-3">
                <label className="block text-xs font-medium text-slate-500 mb-1.5">邮箱</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="email"
                  inputMode="email"
                  className={inputCls}
                />
              </div>
              <div className="mb-3">
                <label className="block text-xs font-medium text-slate-500 mb-1.5">密码</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="至少 6 位"
                  autoComplete={mode === "register" ? "new-password" : "current-password"}
                  className={inputCls}
                />
              </div>

              {error && (
                <div className="mb-3 text-sm text-red-500 font-medium bg-red-50/70 border border-red-200/60 rounded-xl px-3 py-2">
                  {error}
                </div>
              )}

              {mode === "register" && (
                <div className="mb-3 text-xs text-slate-500 leading-relaxed">
                  注册即表示同意服务条款。邮箱验证功能即将上线（本期无需验证）。
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                className="w-full py-3 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 hover:from-blue-400 hover:to-indigo-500 text-white font-semibold text-base shadow-lg transition active:scale-[0.98] disabled:opacity-60"
              >
                {busy ? "请稍候..." : mode === "register" ? "立即注册" : "立即登录"}
              </button>
            </form>

            <button
              onClick={(e) => { e.stopPropagation(); onClose(); }}
              className="mt-4 w-full py-2.5 rounded-xl text-sm text-slate-500 hover:text-slate-700 transition"
            >
              暂不登录，以游客身份继续
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export default AuthModal;
