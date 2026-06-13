/** @type {import('tailwindcss').Config} */
module.exports = {
  // Scan every Jinja2 template and any JS files for class usage.
  // Content paths determine which classes survive purging in production.
  content: [
    './app/templates/**/*.html',
    './app/static/js/**/*.js',
  ],

  theme: {
    extend: {
      // ── Typography (Axle brand — design_handoff_axle_erp) ─────────────────
      // Oswald = display (headings, labels, buttons, nav — uppercase)
      // Barlow = body (descriptions, table text)
      // IBM Plex Mono = part numbers, money, bins, timestamps
      fontFamily: {
        sans: ['Barlow', 'system-ui', 'sans-serif'],
        display: ['Oswald', 'Barlow', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },

      // ── Axle brand palette (mil green / steel / amber) ─────────────────────
      // #5a6630 is brand-700 — the single primary action color (--mil).
      // Do NOT introduce a new primary hue without UI Architect approval.
      colors: {
        brand: {
          '50':  '#f6f7ef',
          '100': '#eef2de',  // selected-row tint (design: .tbl tr.sel)
          '200': '#dce3c3',
          '300': '#c2d178',
          '400': '#9cb23c',  // --mil-bright — focus ring
          '500': '#7d8f3e',
          '600': '#6d7b39',  // --mil-2 — hover
          '700': '#5a6630',  // --mil — PRIMARY: buttons, active nav, brand identity
          '800': '#4a5429',
          '900': '#3c4520',  // --mil-deep
        },
        glow: '#b6cf45',     // --mil-glow — accents on dark surfaces only
        steel: {
          '900': '#191c1f',  // sidebar / dark chrome
          '800': '#22262b',
          '700': '#2f343a',
          '600': '#454b52',
          '500': '#565d65',
          '400': '#7c838b',
          '300': '#a9afb6',
          '200': '#cdd1d5',
          '150': '#dde0e3',
          '100': '#e7e9eb',
        },
        paper: '#f2f3f0',    // workspace background
        ink:   '#0b0c0d',
        // Axle amber — the BRAND accent (Convert-to-Invoice, quote-total rule,
        // core chips). Distinct from Tailwind amber-* which stays the WARNING
        // family per §4 semantics.
        hub: {
          DEFAULT: '#e7891d',  // --amber
          light:   '#ffb24d',  // --amber-2 (dark surfaces)
          soft:    '#fdf3e4',  // --amber-bg (chip backgrounds)
          deep:    '#b06511',  // amber TEXT on light surfaces
        },
      },

      // ── Semantic color contract (§4 of JAKS_UI_Change_Plan.md) ────────────
      // Red    → overdue / critical / error / out-of-stock / discontinued
      // Amber  → warning / low-stock / partial / follow-up / superseded
      // Green  → healthy / paid / success / in-stock / active
      // Blue   → informational / on-order / activity / links
      // Purple → vendor / waiting / serialized / special workflow
      // Orange → core charge / special cost items (hub-* preferred for cores now)
      // Gray   → inactive / archived / neutral / metadata
      //
      // These plus the Axle families above are the ONLY colors permitted in
      // templates. Anything else requires explicit UI Architect approval.

      // 232px sidebar per the Axle shell spec (.erp grid 232px 1fr)
      spacing: {
        'sidebar': '232px',
      },
    },
  },

  plugins: [],
}
