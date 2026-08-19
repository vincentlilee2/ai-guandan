// useGameStream hook 单测：覆盖 SSE 连接管理的关键分支
// （3.1 拆分出来的 hook，此前只有手动浏览器验证，现在用 vitest 固定行为）
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useGameStream } from '../hooks/useGameStream'

// 默认无 token 的 getGameToken；测试中可传自定义函数验证 URL 拼接
const noToken = () => ''

// ---- EventSource mock ----
// 记录每次 new EventSource 的实例，便于驱动 onmessage / onerror
class MockEventSource {
  static instances = []
  url
  onmessage = null
  onerror = null
  _closed = false
  constructor(url) {
    this.url = url
    MockEventSource.instances.push(this)
  }
  close() {
    this._closed = true
  }
}

beforeEach(() => {
  MockEventSource.instances = []
  globalThis.EventSource = MockEventSource
})

afterEach(() => {
  vi.restoreAllMocks()
  delete globalThis.EventSource
})

describe('useGameStream', () => {
  it('连上 SSE 后，收到消息应调用 fetchGameState', async () => {
    const fetchGameState = vi.fn()
    const { result } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-1')
    })
    // 应创建一条到 /api/game-1/stream 的连接
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toBe('/api/game-1/stream')

    // 模拟后端推送一条消息
    act(() => {
      MockEventSource.instances[0].onmessage({ data: '{}' })
    })
    expect(fetchGameState).toHaveBeenCalledTimes(1)
  })

  it('连接 URL 会携带访问 token（v2.5 权限隔离）', async () => {
    const fetchGameState = vi.fn()
    const token = 'tok-abc-123'
    const { result } = renderHook(() => useGameStream(fetchGameState, () => token))
    act(() => {
      result.current.connectGameStream('game-tok')
    })
    expect(MockEventSource.instances[0].url).toBe(`/api/game-tok/stream?token=${encodeURIComponent(token)}`)
  })

  it('浏览器不支持 EventSource 时，直接降级为轮询（调 fetchGameState）', () => {
    delete global.EventSource
    const fetchGameState = vi.fn()
    const { result } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-2')
    })
    expect(fetchGameState).toHaveBeenCalledTimes(1)
    expect(MockEventSource.instances).toHaveLength(0)
  })

  it('连续失败达到上限(3)后降级为轮询', () => {
    vi.useFakeTimers()
    const fetchGameState = vi.fn()
    const { result } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-3')
    })
    const es = MockEventSource.instances[0]
    // 连续触发 3 次 onerror（每次 setTimeout 1s 重试，但我们用 fake timers 跳过）
    act(() => {
      es.onerror()
      es.onerror()
    })
    // 第 3 次失败才降级
    act(() => {
      es.onerror()
    })
    expect(fetchGameState).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('重新连接会先关闭旧连接', () => {
    const fetchGameState = vi.fn()
    const { result } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-a')
    })
    const first = MockEventSource.instances[0]
    act(() => {
      result.current.connectGameStream('game-b')
    })
    expect(first._closed).toBe(true)
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.instances[1].url).toBe('/api/game-b/stream')
  })

  it('组件卸载时关闭 SSE 连接', () => {
    const fetchGameState = vi.fn()
    const { result, unmount } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-x')
    })
    const es = MockEventSource.instances[0]
    act(() => {
      unmount()
    })
    expect(es._closed).toBe(true)
  })

  it('普通网络错误(未主动断开)会安排 1s 后重连', () => {
    vi.useFakeTimers()
    const fetchGameState = vi.fn()
    const { result } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-err')
    })
    const es = MockEventSource.instances[0]
    // 触发一次连接错误：应安排重连，而不是静默死亡
    act(() => {
      es.onerror()
    })
    vi.advanceTimersByTime(1000)
    // 重连 = 新建一条指向同一 gid 的连接
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.instances[1].url).toBe('/api/game-err/stream')
    vi.useRealTimers()
  })

  it('主动断开(换局/新开一局)后不再自动重连', () => {
    vi.useFakeTimers()
    const fetchGameState = vi.fn()
    const { result } = renderHook(() => useGameStream(fetchGameState, noToken))
    act(() => {
      result.current.connectGameStream('game-a')
    })
    const es = MockEventSource.instances[0]
    // 用户主动断开（App 在换局/新开一局时调用）
    act(() => {
      result.current.disconnectGameStream()
    })
    // 旧连接即使触发 onerror，也不应重连旧 gid
    act(() => {
      es.onerror()
    })
    vi.advanceTimersByTime(5000)
    expect(MockEventSource.instances).toHaveLength(1)
    vi.useRealTimers()
  })
})
