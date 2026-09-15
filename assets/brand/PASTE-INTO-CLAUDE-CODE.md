# Paste this into Claude Code

You are redesigning the VaiVia frontend to a new brand system. Everything you
need is in assets/brand/. Read BRAND-SPEC.md fully before writing any code.

## Task

Restyle the existing VaiVia web frontend (chat panel + map, route cards,
composer, attribution footer) to match the brand system in
assets/brand/BRAND-SPEC.md. Do not change product behaviour, routing logic,
API calls, or data flow. This is a visual and copy pass only.

## Order of work

1. Read assets/brand/BRAND-SPEC.md and assets/brand/tokens.css.
2. Wire tokens.css into the app (import it once, globally, before app styles).
   Do not hardcode hex values anywhere in components — use the CSS variables.
3. Replace the logo/wordmark in the header with assets/brand/logo/.
4. Restyle in this order: shell + header, chat transcript, route card,
   hazard block, sources disclosure, composer, map chrome, footer attribution.
5. Swap in assets/brand/icons/ for any inline or icon-font icons.
6. Apply the copy rules in BRAND-SPEC.md section 8 to existing UI strings.
7. Run through the acceptance checklist in section 10 and fix anything failing.

## Hard rules — do not violate

- The app is DARK. Ground is --vv-ground (#0D0F0E). There is no light theme in
  this pass. Lime on white is forbidden.
- Lime (--vv-lime) means route, action, and confirmed data. Flare
  (--vv-flare) means hazard, stale data, and missing coverage. Nothing else
  gets an accent colour. No lime decoration, no flare branding.
- Never claim a route is safe, verified, or suitable for anyone. Never remove
  the OpenStreetMap ODbL attribution.
- Every route must keep a Sources disclosure showing OSM way IDs, the
  Trailforks ID, and the MAPS_TO link distance. The wording "matched by
  proximity, never merged" is deliberate — keep it.
- Square corners everywhere except the app icon and the mic button. No
  drop shadows. Separation comes from 1px hairlines.
- All body text uses --vv-text; all secondary/label text uses --vv-muted.
  Do not introduce a third grey.

## What to tell me when you are done

- Which files you changed.
- Any place the spec was ambiguous and what you chose.
- Anything in the existing UI the spec did not cover.

Ask before adding new sections, pages, or copy that the spec does not define.