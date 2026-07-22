module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
        serif: ["'Fraunces'", "Georgia", "serif"],
        sans: ["'IBM Plex Sans'", "-apple-system", "BlinkMacSystemFont", "sans-serif"],
      },
      colors: {
        risk: {
          low: "#16a34a",
          medium: "#d97706",
          high: "#dc2626",
          critical: "#7f1d1d",
        },
        signal: "#4FD1C5",
        // Day 20 UI pass — nautical chart palette grounded in real
        // Admiralty/IHO chart conventions, not a generic dark theme +
        // single accent. See design notes in AppHeader.tsx.
        chart: {
          navy: "#0A1628",
          panel: "#0F2038",
          hairline: "#1C3352",
        },
        status: {
          critical: "#C81E5C",
          caution: "#C08A3E",
          normal: "#1B8577",
          live: "#4FD1C5",
        },
      },
    },
  },
  plugins: [],
};