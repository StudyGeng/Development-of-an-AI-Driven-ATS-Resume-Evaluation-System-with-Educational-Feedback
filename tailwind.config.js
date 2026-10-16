/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./frontend/**/*.{html,js}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "on-surface": "#191c1d",
        "inverse-primary": "#acc7ff",
        "surface-container-high": "#e7e8e9",
        "on-secondary-fixed": "#001c39",
        "on-surface-variant": "#43474f",
        "surface-dim": "#d9dadb",
        "surface-container-highest": "#e1e3e4",
        background: "#f8f9fa",
        primary: "#002555",
        "on-error-container": "#93000a",
        "on-tertiary": "#ffffff",
        outline: "#747780",
        "on-tertiary-fixed": "#2f1500",
        "on-secondary": "#ffffff",
        "on-primary-fixed-variant": "#28467b",
        "on-tertiary-container": "#db9860",
        tertiary: "#3e1e00",
        "inverse-surface": "#2e3132",
        "tertiary-container": "#5e3000",
        secondary: "#0060ac",
        "tertiary-fixed-dim": "#ffb77d",
        "surface-container": "#edeeef",
        "on-secondary-container": "#003e73",
        "surface-container-lowest": "#ffffff",
        "surface-bright": "#f8f9fa",
        "secondary-fixed-dim": "#a4c9ff",
        "primary-container": "#1b3b6f",
        "secondary-container": "#68abff",
        "surface-container-low": "#f3f4f5",
        "tertiary-fixed": "#ffdcc3",
        "outline-variant": "#c4c6d1",
        "on-tertiary-fixed-variant": "#6b3a09",
        "surface-tint": "#415e94",
        "on-primary": "#ffffff",
        "on-background": "#191c1d",
        "surface-variant": "#e1e3e4",
        surface: "#f8f9fa",
        "secondary-fixed": "#d4e3ff",
        "inverse-on-surface": "#f0f1f2",
        "on-secondary-fixed-variant": "#004883",
        "on-primary-container": "#8aa7e1",
        error: "#ba1a1a",
        "on-error": "#ffffff",
        "on-primary-fixed": "#001a41",
        "error-container": "#ffdad6",
        "primary-fixed-dim": "#acc7ff",
        "primary-fixed": "#d8e2ff"
      },
      borderRadius: {
        DEFAULT: "0.125rem",
        lg: "0.25rem",
        xl: "0.5rem",
        full: "0.75rem"
      },
      fontFamily: {
        headline: ["Manrope", "sans-serif"],
        body: ["Inter", "sans-serif"],
        label: ["Inter", "sans-serif"]
      }
    }
  },
  plugins: [require("@tailwindcss/forms")]
};
