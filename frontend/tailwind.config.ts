import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#172033",
        canvas: "#f7f8fc",
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          500: "#5b5bd6",
          600: "#4f46c7",
          700: "#4338a8"
        }
      },
      boxShadow: {
        soft: "0 16px 50px rgba(31, 41, 55, 0.08)"
      }
    }
  },
  plugins: []
};

export default config;
