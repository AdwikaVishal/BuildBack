/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Black × acid-lime control-center palette
        surface: {
          950: '#0B0B09', // primary background
          900: '#11110F', // secondary background
          850: '#171713', // card background
          800: '#202019', // elevated surface
          700: '#303027', // border
          600: '#3E3E32', // border hover
        },
        acid: {
          DEFAULT: '#D9FF00', // primary accent
          dark:   '#B9E600',  // secondary accent
        },
        ink: {
          DEFAULT:  '#F7F7F0', // primary text
          secondary:'#A3A397', // secondary text
          muted:    '#737368', // muted text
        },
        // Semantic status colors (status ≠ branding)
        border: '#303027', // hairline border alias (border-border / divide-border)
        ok:   '#75E6A0', // success / healthy
        warn: '#FFBF4A', // warning / at risk
        fail: '#FF6464', // failure / blocked
        info: '#75BFFF', // information
        state: {
          blocked:     '#FF6464',
          external:    '#75BFFF',
          at_risk:     '#FFBF4A',
          healthy:     '#75E6A0',
          done:        '#737368',
          pending:     '#A3A397',
          in_progress: '#56A8E0',
        },
      },
      fontFamily: {
        display: ['"Space Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        card: '14px',
      },
      keyframes: {
        'panel-in': {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'panel-in': 'panel-in 180ms ease-out',
      },
    },
  },
  plugins: [],
}
