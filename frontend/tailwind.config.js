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
        'fpl-green': '#00ff87',
        'fpl-purple': '#37003c',
        'fpl-pink': '#e90052',
        // Semantic design tokens (map to CSS variables in index.css)
        bg: 'var(--bg)',
        surface: {
          DEFAULT: 'var(--surface)',
          2: 'var(--surface-2)',
          3: 'var(--surface-3)',
        },
        border: {
          DEFAULT: 'var(--border)',
          strong: 'var(--border-strong)',
        },
        content: {
          DEFAULT: 'var(--text)',
          muted: 'var(--text-muted)',
          subtle: 'var(--text-subtle)',
        },
        primary: {
          DEFAULT: 'var(--primary)',
          600: 'var(--primary-600)',
          fg: 'var(--on-primary)',
        },
        brand: {
          DEFAULT: 'var(--brand)',
          300: 'var(--brand-300)',
        },
        accent: 'var(--accent)',
        magenta: 'var(--magenta)',
        success: 'var(--success)',
        danger: 'var(--danger)',
        warning: 'var(--warning)',
        info: 'var(--info)',
      },
      fontFamily: {
        sans: ['Fira Sans', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['Fira Code', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        'elev-sm': 'var(--shadow-sm)',
        'elev-md': 'var(--shadow-md)',
        'elev-lg': 'var(--shadow-lg)',
      },
    },
  },
  plugins: [],
}
