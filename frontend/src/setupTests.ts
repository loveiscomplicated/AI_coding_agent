import '@testing-library/jest-dom'
import { vi } from 'vitest'

// jsdom은 scrollIntoView를 구현하지 않음
Element.prototype.scrollIntoView = vi.fn()
