// 每日局数常量单测：仅展示用途，本地不限制实际玩牌局数
import { describe, it, expect } from 'vitest'
import { GUEST_LIMIT, MEMBER_LIMIT } from './quota'

describe('quota', () => {
  it('导出常量：游客 5 局 / 会员 20 局（仅展示）', () => {
    expect(GUEST_LIMIT).toBe(5)
    expect(MEMBER_LIMIT).toBe(20)
  })
})
