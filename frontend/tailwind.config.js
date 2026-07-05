/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#060913", panel: "#0b1020", edge: "#1e2a44",
        loom: { blue: "#38bdf8", violet: "#a78bfa", amber: "#fbbf24",
                gold: "#f59e0b", green: "#34d399", red: "#ef4444" },
      },
      boxShadow: { glow: "0 0 12px 2px rgba(56,189,248,0.55)" },
    },
  },
  plugins: [],
};
