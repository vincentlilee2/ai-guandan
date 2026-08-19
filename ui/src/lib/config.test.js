// fetchFeatureFlags 单测：/api/config 拉取 + 各种失败降级为全关
import { describe, it, expect, vi } from 'vitest'
import { fetchFeatureFlags, DEFAULT_FEATURE_FLAGS } from './config'

const okRes = (data) => ({
  ok: true,
  status: 200,
  json: async () => data,
})

describe('fetchFeatureFlags', () => {
  it('200 全开 → 返回 true/true', async () => {
    const fetcher = vi.fn().mockResolvedValue(okRes({ member_login_enabled: true, ai_coach_enabled: true }))
    const flags = await fetchFeatureFlags(fetcher)
    expect(flags).toEqual({ member_login_enabled: true, ai_coach_enabled: true })
  })

  it('200 全关 → 返回 false/false', async () => {
    const fetcher = vi.fn().mockResolvedValue(okRes({ member_login_enabled: false, ai_coach_enabled: false }))
    const flags = await fetchFeatureFlags(fetcher)
    expect(flags).toEqual({ member_login_enabled: false, ai_coach_enabled: false })
  })

  it('非 200（如 500）→ 降级为全关', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    const flags = await fetchFeatureFlags(fetcher)
    expect(flags).toEqual(DEFAULT_FEATURE_FLAGS)
  })

  it('网络异常 → 降级为全关', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('network down'))
    const flags = await fetchFeatureFlags(fetcher)
    expect(flags).toEqual(DEFAULT_FEATURE_FLAGS)
  })

  it('返回体字段缺失/非对象 → 降级为全关', async () => {
    const fetcher = vi.fn().mockResolvedValue(okRes({}))
    const flags = await fetchFeatureFlags(fetcher)
    expect(flags).toEqual(DEFAULT_FEATURE_FLAGS)
  })
})
