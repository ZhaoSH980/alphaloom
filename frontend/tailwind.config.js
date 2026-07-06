/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#050810", panel: "#0a0f1e", edge: "#1b2742",
        grid: "#12305a",
        loom: { blue: "#38bdf8", cyan: "#22d3ee", violet: "#a78bfa", amber: "#fbbf24",
                gold: "#f5a623", green: "#34d399", red: "#f2555a" },
      },
      fontFamily: {
        sans: ["'IBM Plex Sans'", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
        display: ["'Chakra Petch'", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 12px 2px rgba(56,189,248,0.55)",
        "glow-gold": "0 0 16px 2px rgba(245,166,35,0.45)",
        hud: "0 0 0 1px rgba(56,189,248,0.15), 0 8px 30px -8px rgba(0,0,0,0.8)",
      },
      keyframes: {
        boot: { "0%": { opacity: "0", transform: "translateY(8px)" },
                "100%": { opacity: "1", transform: "translateY(0)" } },
        sweep: { "0%": { transform: "translateX(-100%)" },
                 "100%": { transform: "translateX(300%)" } },
        flicker: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.4" } },
      },
      animation: {
        boot: "boot 0.5s cubic-bezier(0.2,0.8,0.2,1) both",
        sweep: "sweep 2.6s linear infinite",
        flicker: "flicker 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
