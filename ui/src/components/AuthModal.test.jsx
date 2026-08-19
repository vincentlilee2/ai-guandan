// AuthModal 单测：渲染/切换 tab/表单提交回调/错误提示
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AuthModal from './AuthModal'

const noop = () => {}

const submitForm = async ({ nickname, email, password, mode }) => {
  // 默认登录模式；注册需先切到注册 tab
  if (mode === 'register') {
    fireEvent.click(screen.getByRole('button', { name: '注册' }))
  }
  const emailInput = screen.getByPlaceholderText('you@example.com')
  const pwdInput = screen.getByPlaceholderText(/至少 6 位/)
  fireEvent.change(emailInput, { target: { value: email } })
  fireEvent.change(pwdInput, { target: { value: password } })
  if (mode === 'register') {
    fireEvent.change(screen.getByPlaceholderText('2-20 个字符'), { target: { value: nickname } })
  }
  fireEvent.click(screen.getByRole('button', { name: mode === 'register' ? '立即注册' : '立即登录' }))
  return { emailInput, pwdInput }
}

describe('AuthModal', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    global.fetch = vi.fn()
  })

  it('未打开时不渲染', () => {
    const { container } = render(<AuthModal open={null} onClose={noop} onSuccess={noop} onLogout={noop} authUser={null} initialMode="login" />)
    expect(container.querySelector('.auth-modal')).toBeNull()
  })

  it('打开后渲染登录/注册 tab 与表单字段', () => {
    render(<AuthModal open="login" onClose={noop} onSuccess={noop} onLogout={noop} authUser={null} initialMode="login" />)
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '注册' })).toBeTruthy()
    expect(screen.getByPlaceholderText('you@example.com')).toBeTruthy()
    expect(screen.getByPlaceholderText(/至少 6 位/)).toBeTruthy()
    // 登录模式不显示昵称输入
    expect(screen.queryByPlaceholderText('2-20 个字符')).toBeNull()
  })

  it('注册 tab 显示昵称输入', () => {
    render(<AuthModal open="login" onClose={noop} onSuccess={noop} onLogout={noop} authUser={null} initialMode="login" />)
    fireEvent.click(screen.getByRole('button', { name: '注册' }))
    expect(screen.getByPlaceholderText('2-20 个字符')).toBeTruthy()
  })

  it('登录表单提交调用 onSuccess 并传回 user', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ token: 't1', nickname: '小明', email: 'm@x.com' }),
    })
    const onSuccess = vi.fn()
    render(<AuthModal open="login" onClose={noop} onSuccess={onSuccess} onLogout={noop} authUser={null} initialMode="login" />)
    await submitForm({ email: 'm@x.com', password: 'secret1', mode: 'login' })
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    expect(onSuccess).toHaveBeenCalledWith({ token: 't1', nickname: '小明', email: 'm@x.com' })
    expect(global.fetch).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({
      body: JSON.stringify({ email: 'm@x.com', password: 'secret1' }),
    }))
  })

  it('注册表单提交走 /api/auth/register 并带昵称', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ token: 't2', nickname: '小明', email: 'm@x.com' }),
    })
    const onSuccess = vi.fn()
    render(<AuthModal open="login" onClose={noop} onSuccess={onSuccess} onLogout={noop} authUser={null} initialMode="login" />)
    await submitForm({ nickname: '小明', email: 'm@x.com', password: 'secret1', mode: 'register' })
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    expect(global.fetch).toHaveBeenCalledWith('/api/auth/register', expect.objectContaining({
      body: JSON.stringify({ nickname: '小明', email: 'm@x.com', password: 'secret1' }),
    }))
  })

  it('前端校验：邮箱格式错误时内联报错，不发请求', async () => {
    render(<AuthModal open="login" onClose={noop} onSuccess={noop} onLogout={noop} authUser={null} initialMode="login" />)
    await submitForm({ email: 'bad-email', password: 'secret1', mode: 'login' })
    expect(screen.getByText('请输入正确的邮箱地址')).toBeTruthy()
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('密码过短时内联报错', async () => {
    render(<AuthModal open="login" onClose={noop} onSuccess={noop} onLogout={noop} authUser={null} initialMode="login" />)
    await submitForm({ email: 'm@x.com', password: '123', mode: 'login' })
    expect(screen.getByText('密码至少需要 6 位')).toBeTruthy()
  })

  it('后端返回错误时展示 detail', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: '邮箱或密码不正确' }),
    })
    render(<AuthModal open="login" onClose={noop} onSuccess={noop} onLogout={noop} authUser={null} initialMode="login" />)
    await submitForm({ email: 'm@x.com', password: 'wrong1', mode: 'login' })
    await waitFor(() => expect(screen.getByText('邮箱或密码不正确')).toBeTruthy())
  })

  it('已登录账号态：显示昵称/邮箱/今日局数 + 退出登录', () => {
    const onLogout = vi.fn()
    const { container } = render(<AuthModal open="account" onClose={noop} onSuccess={noop} onLogout={onLogout} authUser={{ nickname: '小明', email: 'm@x.com', plays_today: 3, limit: 20 }} initialMode="login" />)
    expect(screen.getByText('小明')).toBeTruthy()
    expect(screen.getByText('m@x.com')).toBeTruthy()
    // "3 / 20 局" 由多个元素拼成，用整体文本断言
    expect(container.textContent).toContain('3 / 20 局')
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
    expect(onLogout).toHaveBeenCalled()
  })

  it('账号态展示额度已用尽的提示（本地不限局数）', () => {
    render(<AuthModal open="account" onClose={noop} onSuccess={noop} onLogout={noop} authUser={{ nickname: '小明', email: 'm@x.com', plays_today: 20, limit: 20 }} initialMode="login" />)
    expect(screen.getByText('今日额度已用完，仍可继续玩（不限局数）')).toBeTruthy()
  })
})
