// useAudio hook 单测：用 jsdom mock 验证关键行为（不依赖真实音频设备）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useAudio } from '../hooks/useAudio'

// ---- jsdom 下的 Audio / Web Audio / speechSynthesis mock ----
class MockAudio {
  constructor(src) { this.src = src; this.volume = 1; this._play = vi.fn(() => Promise.resolve()) }
  play() { return this._play() }
  pause() {}
  load() {}
}
class MockAudioContext {
  constructor() { this.state = 'running'; this.currentTime = 0; this.sampleRate = 44100; this.destination = {} }
  resume() { return Promise.resolve() }
  createOscillator() { return { type: '', frequency: { value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {}, start() {}, stop() {} } }
  createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} } }
  createBuffer() { return { getChannelData: () => new Float32Array(10) } }
  createBufferSource() { return { buffer: null, connect() {}, start() {}, stop() {} } }
  createBiquadFilter() { return { type: '', frequency: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} } }
  createWaveShaper() { return { curve: null, connect() {} } }
}

beforeEach(() => {
  global.Audio = MockAudio
  global.AudioContext = MockAudioContext
  global.window.AudioContext = MockAudioContext
  global.window.speechSynthesis = { cancel: vi.fn(), speak: vi.fn(), getVoices: () => [] }
  global.window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t } }
})

function makeRefs() {
  const mk = (v) => ({ current: v })
  return {
    audioUnlockedRef: mk(false),
    audioPoolRef: mk(new Map()),
    winCelebrationPlayedRef: mk(new Set()),
    winVoiceTimerRef: mk(null),
    gameOverVoiceTimerRef: mk(null),
  }
}

describe('useAudio', () => {
  it('返回 6 个音频方法', () => {
    const { result } = renderHook(() => useAudio(makeRefs()))
    for (const k of ['unlockAudio', 'playSynthesizedCheer', 'speakWithGirlVoice', 'playWinCelebration', 'playGameOverSummary', 'playExplosionSound']) {
      expect(typeof result.current[k]).toBe('function')
    }
  })

  it('unlockAudio 创建 AudioContext 并预热 global_voice_player', async () => {
    const refs = makeRefs()
    const { result } = renderHook(() => useAudio(refs))
    await act(async () => { result.current.unlockAudio() })
    await Promise.resolve() // 等 play().then(...) 微任务落地
    // 预热对象已写入 pool（证明 AudioContext 创建 + 预热路径执行）
    expect(refs.audioPoolRef.current.has('global_voice_player')).toBe(true)
    // 首次解锁后标记翻转
    expect(refs.audioUnlockedRef.current).toBe(true)
  })

  it('speakWithGirlVoice 调用 speechSynthesis.speak', () => {
    const { result } = renderHook(() => useAudio(makeRefs()))
    act(() => { result.current.speakWithGirlVoice('测试语音') })
    expect(global.window.speechSynthesis.speak).toHaveBeenCalled()
  })

  it('playExplosionSound 调用 AudioContext 创建噪声/振荡器（不抛错）', () => {
    const { result } = renderHook(() => useAudio(makeRefs()))
    expect(() => act(() => { result.current.playExplosionSound() })).not.toThrow()
  })
})
