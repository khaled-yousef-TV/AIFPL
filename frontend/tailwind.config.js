/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'fpl-green': '#00ff87',
        'fpl-purple': '#37003c',
        'fpl-pink': '#e90052',
      },
    },
  },
  plugins: [],
}

