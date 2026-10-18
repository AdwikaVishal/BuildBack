/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark ops-dashboard palette
        surface: {
          900: '#0a0e1a',
          800: '#0f1629',
          700: '#141d35',
          600: '#1a2440',
          500: '#202c4d',
          400: '#273660',
        },
        accent: {
          blue:   '#3b82f6',
          cyan:   '#06b6d4',
          purple: '#8b5cf6',
          green:  '#10b981',
          yellow: '#f59e0b',
          red:    '#ef4444',
          orange: '#f97316',
        },
        state: {
          blocked:  '#ef4444',
          external: '#f97316',
          at_risk:  '#f59e0b',
          healthy:  '#10b981',
          done:     '#6b7280',
          pending:  '#8b5cf6',
          in_progress: '#3b82f6',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
