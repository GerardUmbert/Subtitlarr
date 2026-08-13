// Shared Tailwind CDN config for every docs/*.html page — ports Subtitlarr's
// own CSS custom-property palette (see site.css) into Tailwind's theme
// instead of introducing a separate color system. Values are the LIGHT
// palette; dark mode is still handled by site.css's existing
// `@media (prefers-color-scheme: dark)` block redefining the same custom
// properties, so Tailwind utilities that reference var(--token) pick up
// the dark values automatically with no separate dark: variants needed.
tailwind.config = {
  // site.css is a separate linked stylesheet, unlike a typical Tailwind
  // page where all CSS lives in one place after Tailwind in the cascade.
  // The Play CDN script injects its own <style> at runtime, appended to
  // <head> AFTER site.css's <link> regardless of original tag order —
  // its preflight reset (which zeroes out heading sizes/margins, resets
  // font-family, etc.) was silently overriding site.css's typography and
  // spacing site-wide. Disabling it here keeps Tailwind's utility classes
  // available without it clobbering the existing hand-written design.
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        surface2: 'var(--surface-2)',
        border: 'var(--border)',
        ink: 'var(--ink)',
        'ink-dim': 'var(--ink-dim)',
        'ink-faint': 'var(--ink-faint)',
        accent: 'var(--accent)',
        'accent-ink': 'var(--accent-ink)',
        'accent-soft': 'var(--accent-soft)',
        good: 'var(--good)',
        warn: 'var(--warn)',
        bad: 'var(--bad)',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'Consolas', 'monospace'],
      },
    },
  },
}
