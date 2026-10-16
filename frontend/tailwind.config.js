/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "'SF Pro Text'", "'Segoe UI'", "Roboto", "Helvetica", "Arial", "sans-serif"],
      },
      colors: {
        vault: {
          50: "#f5f7ff", 100: "#e9edff", 200: "#cfd7ff", 300: "#a9b6ff", 400: "#7f8cff",
          500: "#5b63f5", 600: "#4643e0", 700: "#3a35bb", 800: "#2f2d93", 900: "#292a74",
        },
      },
      boxShadow: { soft: "0 1px 2px rgba(0,0,0,.06), 0 8px 24px -12px rgba(15,23,42,.18)" },
    },
  },
  plugins: [],
};
