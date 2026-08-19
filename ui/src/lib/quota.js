// 每日玩牌局数常量（仅展示用途）：
// 本地系统不限制实际玩牌局数——游客/会员均可无限玩。
// 会员当日局数由服务器权威记录（官网或本地 store，/api/auth/me 返回 plays_today），
// 前端只做软提醒展示（达 MEMBER_LIMIT 后仍可继续玩）。

export const GUEST_LIMIT = 5;
export const MEMBER_LIMIT = 20;
