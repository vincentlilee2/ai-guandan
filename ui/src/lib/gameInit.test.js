// 复盘轮转折叠 helper 单测：replaySkipTarget / replayPrevTarget
import { describe, it, expect } from 'vitest'
import { replaySkipTarget, replayPrevTarget } from './gameInit'

const PLAY = (player, desc) => ({ player, action: 'PLAY', desc, cards: [desc] })
const PASS = (player) => ({ player, action: 'PASS' })
const ROUND_END = (winner) => ({ action: 'ROUND_END', winner })

// 一手完整轮次：User 出对子 → 三 PASS → ROUND_END
const roundUserWins = [
  PLAY('User', '对K'),
  PASS('RightBot'),
  PASS('PartnerBot'),
  PASS('LeftBot'),
  ROUND_END('User'),
  PLAY('User', '一张3'), // 下一轮首发
]

describe('replaySkipTarget', () => {
  it('空 history 返回 -1', () => {
    expect(replaySkipTarget([], 0)).toBe(-1)
  })

  it('轮末 PASS 尾巴折叠到下一轮首发', () => {
    // 停在 PASS 串上时，若整段 PASS 以 ROUND_END 收尾（= 前一手 PLAY 是轮末赢家手），
    // 折叠到下一轮首发（跳过 PASS 与 ROUND_END）。
    expect(replaySkipTarget(roundUserWins, 2)).toBe(5)
    expect(replaySkipTarget(roundUserWins, 3)).toBe(5)
  })

  it('停在 ROUND_END 上跳到下一手', () => {
    // index=4（ROUND_END）→ 一张3 (index 5)
    expect(replaySkipTarget(roundUserWins, 4)).toBe(5)
  })

  it('中途 PASS（后面还有人出牌）照常停靠', () => {
    const seq = [PLAY('User', '一张4'), PASS('RightBot'), PLAY('RightBot', '一张5')]
    expect(replaySkipTarget(seq, 1)).toBe(1) // 非轮末尾巴 → 正常停靠 PASS
  })

  it('PLAY 停靠（不进尾部折叠）', () => {
    expect(replaySkipTarget(roundUserWins, 0)).toBe(0) // 轮末赢家手本身仍要展示
  })

  it('到末尾后钳制在最后一手', () => {
    const seq = [PLAY('User', '一张4'), ROUND_END('User')]
    expect(replaySkipTarget(seq, 1)).toBe(1) // ROUND_END 越过末尾 → 落回最后下标
  })
})

describe('replayPrevTarget', () => {
  it('找前一个 PLAY', () => {
    const seq = [PLAY('User', '一张4'), PASS('RightBot'), PLAY('RightBot', '一张5')]
    expect(replayPrevTarget(seq, 2)).toBe(0)
    expect(replayPrevTarget(seq, 1)).toBe(0)
  })

  it('空 history 返回 -1', () => {
    expect(replayPrevTarget([], 0)).toBe(-1)
  })

  it('已在第一手时返回 -1', () => {
    const seq = [PLAY('User', '一张4')]
    expect(replayPrevTarget(seq, 0)).toBe(-1)
  })
})
