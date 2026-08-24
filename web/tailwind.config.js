/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Tek kaynak `styles.css` icindeki degiskenler. Tailwind yardimci
      // siniflarindan da AYNI degerlere ulasilabilsin diye buraya baglandi;
      // ikinci bir palet tanimlamak, ikisinin sessizce ayrisacagi bir yer
      // daha yaratirdi.
      fontFamily: {
        sans: ["Archivo", "Segoe UI", "system-ui", "sans-serif"],
        serif: ["Literata", "Georgia", "serif"],
      },
      colors: {
        ground: "var(--ground)",
        surface: "var(--surface)",
        ink: {
          DEFAULT: "var(--ink)",
          2: "var(--ink-2)",
          3: "var(--ink-3)",
        },
        rule: {
          DEFAULT: "var(--rule)",
          2: "var(--rule-2)",
        },
        board: {
          DEFAULT: "var(--board)",
          2: "var(--board-2)",
          3: "var(--board-3)",
        },
        chalk: {
          DEFAULT: "var(--chalk)",
          2: "var(--chalk-2)",
          3: "var(--chalk-3)",
        },
        signal: {
          DEFAULT: "var(--signal)",
          ink: "var(--signal-ink)",
          soft: "var(--signal-soft)",
        },
      },
      borderRadius: {
        sm: "var(--r-sm)",
        DEFAULT: "var(--r)",
        lg: "var(--r-lg)",
      },
      boxShadow: {
        1: "var(--shadow-1)",
        2: "var(--shadow-2)",
        3: "var(--shadow-3)",
      },
    },
  },
  plugins: [],
};
