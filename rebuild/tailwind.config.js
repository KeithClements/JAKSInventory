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
      // ── Typography ─────────────────────────────────────────────────────────
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },

      // ── Brand palette (army olive / dark green) ────────────────────────────
      // #4b5320 is brand-700 — the single primary action color.
      // Do NOT introduce a new primary hue without UI Architect approval.
      colors: {
        brand: {
          '50':  '#f4f5e9',
          '100': '#e6e9cc',
          '200': '#cdd399',
          '300': '#b0be71',
          '400': '#94a84e',  // focus ring
          '500': '#788436',
          '600': '#5e6928',  // hover
          '700': '#4b5320',  // PRIMARY — buttons, active tabs, brand identity
          '800': '#363c18',
          '900': '#232710',
        },
      },

      // ── Semantic color contract (§4 of JAKS_UI_Change_Plan.md) ────────────
      // Red    → overdue / critical / error / out-of-stock / discontinued
      // Amber  → warning / low-stock / partial / follow-up / superseded
      // Green  → healthy / paid / success / in-stock / active
      // Blue   → informational / on-order / activity / links
      // Purple → vendor / waiting / serialized / special workflow
      // Orange → core charge / special cost items
      // Gray   → inactive / archived / neutral / metadata
      //
      // These are the ONLY color families permitted in templates. Any use of a
      // color outside this table requires explicit UI Architect approval.
      // (Colors are standard Tailwind; no overrides needed — listed for docs.)
    },
  },

  plugins: [],
}
