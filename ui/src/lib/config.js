// 功能开关拉取：/api/config 返回 { member_login_enabled, ai_coach_enabled }。
// 约定：任何失败（非 200 / 网络异常 / 字段缺失）都按「关闭」降级，
// 保证无后端或后端未配置时前端保持保守（不显示登录/AI 教练入口）。

export const DEFAULT_FEATURE_FLAGS = {
  member_login_enabled: false,
  ai_coach_enabled: false,
};

export const fetchFeatureFlags = async (fetcher = globalThis.fetch) => {
  try {
    const res = await fetcher('/api/config');
    if (!res.ok) return { ...DEFAULT_FEATURE_FLAGS };
    const data = await res.json().catch(() => null);
    if (!data || typeof data !== 'object') return { ...DEFAULT_FEATURE_FLAGS };
    return {
      member_login_enabled: data.member_login_enabled === true,
      ai_coach_enabled: data.ai_coach_enabled === true,
    };
  } catch (_) {
    return { ...DEFAULT_FEATURE_FLAGS };
  }
};
