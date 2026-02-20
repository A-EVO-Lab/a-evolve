import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "panel-bg": "rgb(15 23 42)",
        "panel-border": "rgb(51 65 85)",
        "score-good": "rgb(34 197 94)",
        "score-mid": "rgb(234 179 8)",
        "score-bad": "rgb(239 68 68)"
      }
    }
  },
  darkMode: "class",
  plugins: []
} satisfies Config;
