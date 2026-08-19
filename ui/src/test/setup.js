import '@testing-library/jest-dom/vitest'

// jsdom 未实现 PointerEvent：polyfill 一份，让 fireEvent.pointerDown/Move/Up
// 能携带 clientX/clientY/pointerId，支撑手牌拖拽等指针交互测试。
if (!window.PointerEvent) {
  class PointerEvent extends MouseEvent {
    constructor(type, params = {}) {
      super(type, params)
      Object.defineProperty(this, 'pointerId', { value: params.pointerId ?? 1, writable: true })
      Object.defineProperty(this, 'isPrimary', { value: params.isPrimary ?? true, writable: true })
      Object.defineProperty(this, 'pointerType', { value: params.pointerType ?? 'mouse', writable: true })
      // buttons 在 MouseEvent 上是只读 getter，仅在未由 init 设置时补缺省
      if (this.buttons === 0 && params.buttons != null) {
        Object.defineProperty(this, 'buttons', { value: params.buttons, writable: true })
      }
    }
  }
  window.PointerEvent = PointerEvent
}
if (!window.Element.prototype.setPointerCapture) {
  window.Element.prototype.setPointerCapture = function () {}
  window.Element.prototype.releasePointerCapture = function () {}
  window.Element.prototype.hasPointerCapture = function () { return false }
}

