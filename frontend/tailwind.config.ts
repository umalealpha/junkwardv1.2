import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          950: '#0a0f1e',
          900: '#0f172a',
          800: '#1e293b',
          700: '#253147',
          600: '#334155',
        },
        brand: {
          DEFAULT: '#ea580c',
          hover: '#c2410c',
          light: '#fb923c',
          muted: '#7c2d12',
        },
      },
      fontFamily: {
        // CFO directive 2026-05-17: Book Antiqua across the whole ERP.
        // Tailwind `font-sans` is mapped to Book Antiqua so existing class
        // usage doesn't need to change. Fallbacks cover macOS / Linux.
        sans: ['"Book Antiqua"', '"Palatino Linotype"', 'Palatino', '"URW Palladio L"', 'Georgia', 'serif'],
        serif: ['"Book Antiqua"', '"Palatino Linotype"', 'Palatino', '"URW Palladio L"', 'Georgia', 'serif'],
      },
      animation: {
        'fade-in': 'fadeIn 0.2s ease-in-out',
        'slide-in': 'slideIn 0.3s ease-out',
        'spin-slow': 'spin 1.5s linear infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideIn: {
          '0%': { transform: 'translateY(-10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
      boxShadow: {
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -2px rgba(0, 0, 0, 0.3)',
        'card-hover': '0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -4px rgba(0, 0, 0, 0.4)',
        'orange-glow': '0 0 20px rgba(234, 88, 12, 0.3)',
      },
    },
  },
  plugins: [],
}

export default config
