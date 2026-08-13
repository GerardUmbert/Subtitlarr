/** @type {import('tailwindcss').Config} */
// Real build (not the Play CDN runtime script) — see assets/tailwind.build.css
// for the source entry point and package.json's "build:css" script. Ports
// Subtitlarr's own CSS custom-property palette (site.css) into Tailwind's
// theme instead of introducing a separate color system; dark mode is
// handled by site.css's existing `@media (prefers-color-scheme: dark)`
// block redefining the same custom properties, so utilities that reference
// var(--token) pick up dark values automatically with no dark: variants.
module.exports = {
  content: ["./*.html"],
  // Disabled: site.css is a separate, pre-existing hand-written stylesheet.
  // Tailwind's preflight reset (heading sizes/margins, font-family, img
  // sizing, etc.) is meant for pages where Tailwind owns all base styles —
  // here it would silently override site.css's typography and spacing.
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        surface2: "var(--surface-2)",
        border: "var(--border)",
        ink: "var(--ink)",
        "ink-dim": "var(--ink-dim)",
        "ink-faint": "var(--ink-faint)",
        accent: "var(--accent)",
        "accent-ink": "var(--accent-ink)",
        "accent-soft": "var(--accent-soft)",
        good: "var(--good)",
        warn: "var(--warn)",
        bad: "var(--bad)",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", '"Segoe UI"', "Roboto", "sans-serif"],
        mono: ['"IBM Plex Mono"', "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
