import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#0b0f14",
        panel: "#121821",
        border: "#1f2937",
        accent: "#22c55e",
        danger: "#ef4444",
        warn: "#f59e0b",
        muted: "#8b98a9",
      },
    },
  },
  plugins: [],
};

export default config;
