import { defineConfig } from 'vitest/config'
import path from 'node:path'

// A deliberately small harness. It exists to guard the sign-in path, which has
// twice been broken in a way that neither the type checker nor the backend
// could see (a page simply forgetting to ask a guard). Keep it fast so it can
// run on every push.
export default defineConfig({
  // Tailwind's PostCSS plugin is Next-specific and Vite cannot load it. These
  // are pure logic tests with no styling, so point PostCSS at nothing.
  css: { postcss: { plugins: [] } },
  // The frozen-design test renders a real component, so JSX must compile with
  // the modern runtime — without this esbuild uses the classic transform and
  // every component import dies on "React is not defined".
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    include: ['src/**/__tests__/**/*.test.ts'],
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
})
