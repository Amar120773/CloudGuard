import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

// jsdom implements neither of these, and the charts/layout depend on them.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {}
}
if (!window.scrollTo) {
  window.scrollTo = () => {}
}

// Charts measure their container; jsdom reports 0 for everything.
Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
  return {
    width: 800, height: 300, top: 0, left: 0, bottom: 300, right: 800, x: 0, y: 0,
    toJSON: () => {},
  }
}

// jsdom implements no layout, so scrollIntoView is absent. The palette guards
// the call, but stubbing it keeps the keyboard-navigation tests realistic.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}
