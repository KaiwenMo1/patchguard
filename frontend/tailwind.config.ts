import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      boxShadow: {
        "soft-ring": "0 18px 55px rgba(20, 28, 48, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
