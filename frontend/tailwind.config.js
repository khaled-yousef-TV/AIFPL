/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // FPL brand
        'fpl-purple': '#37003c',
        // Semantic design tokens (map to CSS variables in index.css)
        bg: 'var(--paper)',
        surface: {
          DEFAULT: 'var(--paper-2)',
          2: 'var(--paper-3)',
          3: 'var(--rule-soft)',
        },
        border: {
          DEFAULT: 'var(--rule)',
          strong: 'var(--rule-strong)',
        },
        content: {
          DEFAULT: 'var(--ink)',
          muted: 'var(--ink-muted)',
          subtle: 'var(--ink-subtle)',
        },
        primary: {
          DEFAULT: 'var(--purple)',
          600: 'var(--purple-2)',
          fg: 'var(--cream)',
        },
        brand: {
          DEFAULT: 'var(--purple)',
          300: 'var(--purple-2)',
        },
        accent: 'var(--orange)',
        // Kept as a token name for the captain / highlight accent
        magenta: 'var(--orange)',
        success: 'var(--up)',
        danger: 'var(--down)',
        warning: 'var(--warn)',
        info: 'var(--purple)',
        cream: 'var(--cream)',
      },
      fontFamily: {
        sans: ['Archivo Variable', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      // The programme is printed, not floating: no elevation scale.
      boxShadow: {
        'elev-sm': 'none',
        'elev-md': 'none',
        'elev-lg': 'none',
      },
    },
  },
  plugins: [],
}
