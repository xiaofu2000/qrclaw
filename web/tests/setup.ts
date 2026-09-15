import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

/** 每个组件用例结束后清理 DOM 和浏览器存储。 */
afterEach(() => {
  cleanup()
  localStorage.clear()
})
