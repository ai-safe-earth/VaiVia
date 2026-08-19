# VaiVia brand system — frontend spec

Version 1.0 · acid direction · dark ground

This document is the source of truth for the visual system. It is written to be
implemented directly. Where it gives a number, use that number.

---

## 1. The idea

VaiVia is an instrument, not an outdoor lifestyle brand. It reads terrain
honestly and says what it does and does not know. The visual system is
technical: a dark ground, a hard grid of 1px hairlines, one loud accent for
action and one for danger, mono type for anything measured.

Two consequences that drive every decision below:

- Acid only reads as acid on dark. Lime on a light ground looks like a
  highlighter. The ground is near-black and stays that way.
- Colour carries meaning, not mood. If something is lime, you can act on it or
  the graph is confident about it. If something is flare, you need to know it
  before you go.

---

## 2. Colour

    --vv-ground   #0D0F0E   page background, pin cores, text on lime
    --vv-panel    #1A1E1B   inset panels, quoted user message, hairline steps
    --vv-line     #2A2E2B   structural hairlines (1px)
    --vv-line-2   #1A1E1B   internal/secondary hairlines (1px)
    --vv-text     #F2F3F0   all primary text and default icon strokes
    --vv-muted    #A7ADA6   labels, captions, placeholders, secondary text
    --vv-lime     #CCFF3B   route, primary action, confirmed data
    --vv-flare    #FF6B3D   hazard, stale data, missing coverage
    --vv-map      #12150F   map canvas base

Light fallback (print, email, third-party embeds only — NOT the app):

    --vv-ground-l #F2F3F0   --vv-text-l  #0D0F0E   --vv-flare-l #C4451F

Lime has no light-ground equivalent. On light, action becomes ink-on-paper with
a 1px border; flare darkens to #C4451F for text.

### Rules

- Buttons: lime fill, ground text. Never lime text on ground for a button.
- Hazard: flare is a 6px left bar plus a flare uppercase label. The hazard
  sentence itself stays --vv-text at body size. Do not enlarge it, do not use
  an icon-with-triangle alarm, do not use red.
- Contrast: every pairing above clears WCAG AA at 9px. Do not tint text with
  opacity — use --vv-muted. Opacity is reserved for the coverage texture.
- Two accents only. Do not add a third for any reason.

---

## 3. Type

Two families. No third.

    Sans   "Helvetica Neue", Helvetica, sans-serif
    Mono   "IBM Plex Mono", ui-monospace, monospace

Scale — use these exact values:

    Display XL   700  52px / 0.95  / -0.05em  uppercase   sans   hero only
    Display      700  44px / 0.95  / -0.05em  uppercase   sans   screen title
    Title        700  28-30px / 0.98 / -0.045em uppercase sans   route name
    Figure       700  25-30px / 1   / -0.04em            sans   numbers
    Subtitle     600  19px / 1.2   / -0.02em            sans
    Body         400  13.5px / 1.55                     sans
    Body small   400  12-13px / 1.5                     sans
    Label        400  9.5px / 1    / +0.11em  uppercase sans   section labels
    Data         400  11.5-12px / 1.5                   mono   IDs, coordinates

### Rules

- Only the display steps are loud. Labels stay 9.5px, data stays mono. Do not
  scale up the technical layer to match the headlines.
- Numbers lead, units follow: the figure at Figure size, then the unit beneath
  it as a Label (KM, M UP, WALKING, SAC).
- The first figure in a set is lime; the rest are --vv-text.
- Never uppercase body copy. Never letterspace body copy.
- Wordmark is live text, not an image, wherever it sits next to text: 700,
  -0.05em, lowercase, "vai" in --vv-text and "via" in --vv-lime.

---

## 4. Grid, spacing, borders

- Spacing scale: 6, 9, 12, 14, 16, 18, 20, 22, 26, 34, 46px. Pick from it.
- Panel padding: 16px 18px on mobile, 16px 20px on web.
- Chat panel width on desktop: 400px fixed, map takes the rest.
- Borders: 1px solid var(--vv-line) between structural blocks;
  1px solid var(--vv-line-2) between rows inside a block.
- border-radius: 0 everywhere. Exceptions: app icon (platform mask) and the
  mic button (circle) — nothing else.
- No box-shadow anywhere.

---

## 5. Components

### Header
54px tall, bottom hairline. Mark 17px, wordmark 18px, then region label
(Label style, --vv-muted) at 26px gap. Right side: nav links as Labels in
--vv-muted; the account link is a lime block with ground text, 7px 12px.

### Chat transcript
No bubbles. The transcript is a ruled document: each turn is a full-width
block separated by hairlines.

- User message: --vv-panel background, a "YOU ASKED" Label above it in
  --vv-muted, message in Body/--vv-text.
- Assistant message: no background, Body/--vv-text, 16px 18-20px padding.
  Named places and times that matter may be lime inline.

### Query interpretation block ("How I read it")
A Label header, then a 2-column grid of parsed constraints in Data style:
key in --vv-muted, value in lime. Below it, one Body-small line offering to
edit a value instead of rewriting the question, with the action in lime and a
1px lime bottom border.

This block is load-bearing: it shows the user what the system understood.
Keep it even if it looks redundant.

### Route card
No border, no radius — it is a band in the transcript delimited by hairlines.

1. Route name at Title size, uppercase, 20px top padding.
2. Figure row: distance (lime), ascent, walking time, SAC grade. Units as
   Labels beneath each figure. Flex row, first three flex:1, grade flex:none.
3. Optional two-column key-facts row (hut / return transport), Body-small,
   secondary line in --vv-muted, split by a --vv-line-2 hairline.

### Hazard block
A flex row: a 6px flare bar as the first child, full height, then the content
at 15px 18-20px. Inside: "BEFORE YOU GO" as a Label in flare, then the hazard
sentences in Body/--vv-text.

Write hazards as plain observations with numbers: what, where, how old the
information is. No exclamation marks, no bold, no icons.

### Sources disclosure
Collapsed by default: one row, Label style in --vv-text, count on the left, a
lime plus sign right-aligned. Expanded: --vv-panel background, same row with a
lime minus, then Data-style rows:

    geometry   OSM ways <ids>
    grade      Trailforks #<id>
    link       MAPS_TO @ <n> m · never merged   <- "never merged" in flare

Then a closing line above a hairline, in --vv-muted, sans: how old the
conditions are, that they are crowd-reported, and that VaiVia does not verify
them.

The two data sources are linked by proximity and never merged. Say so here.
This is the product differentiator and it lives in the UI, not the marketing.

### Composer
Bottom bar, top hairline, flex row, no radius. Mic button 66x62 lime block
with a ground-stroke mic icon (mobile: circle allowed). Input area flush,
placeholder in --vv-muted at 13.5px. Web variant: text input flex:1 plus a
lime "ASK" block, Label-ish 12px 600 +0.06em uppercase, ground text.

### Buttons
    Primary     lime fill, ground text, 600 12.5px, +0.06em, uppercase, 19px 18px
    Secondary   transparent, --vv-text, left hairline, same metrics

Full-width primary sits at the bottom edge of a screen with no gap and no
radius. Buttons never have icons.

### Map chrome
Layer tabs sit as a flush row along the top of the map with hairline dividers;
the active tab is a lime block with ground text, inactive are --vv-text,
unavailable are --vv-muted.

Bottom panel: elevation profile as 2px-gap bars, --vv-lime, with hazard
segments in --vv-flare. Below the bars, three Labels — start, max, end.
Attribution is the last row, Body-small in --vv-muted, above a hairline.

### Map style (for the tile layer / route rendering)
Base canvas --vv-map. Route line --vv-lime at 3px, no casing needed on dark.
Hazard segments overlaid as --vv-flare dashes. Pins are 30px squares — never
teardrops — with a --vv-ground core: lime border means on your route, --vv-text
border means nearby, flare means hazard and is always filled. The current
position is a 14px lime circle with a 2px ground ring.

### Coverage
Coverage is a map texture, not an error state: the same lime grid at three
densities — dense (graded, named, linked), thin (OSM only, no grades), none
(we do not route here) — dropping in opacity 1 / 0.75 / 0.45.

When a request reaches past coverage, say where it stops by name, and offer
what is inside it. Do not fail silently and do not guess beyond the data.

---

## 6. Icons

assets/brand/icons/ — 20x20 viewBox, 1.75px stroke, no fill, stroke
currentColor. Render at 22 or 26px. Set colour by CSS: default --vv-text,
hazard and exposure --vv-flare, trail --vv-lime when it marks the active route.

    trail  rifugio  station  summit  lake  funivia  time  ascent  hazard  exposure

Difficulty uses SAC T1-T6 as 8px rotated squares: lime up to the user stated
limit, flare beyond it, hollow --vv-muted for out-of-scope T5-T6. Never invent
MTB grades for hiking routes.

Do not add icons that are not in this set without asking.

---

## 7. Logo

assets/brand/logo/

    vaivia-mark.svg          two blocks and a rule; lime block on dark
    vaivia-mark-ground.svg   ground-on-lime, for use inside a lime area
    vaivia-app-icon.svg      lime ground, ground-coloured mark, 1024
    vaivia-wordmark.svg      live-text wordmark, split colour

The mark is two nodes and one edge: it is the data model — two sources,
matched, never merged. Minimum 16px. Clear space equals the wordmark x-height.
At 40px and below the lime block reads as part of the diagonal; that is fine.

Never: outline the mark, rotate it, put it on a photo without a solid block,
recolour it outside the palette, or set the wordmark in another family.

---

## 8. Copy rules

The voice is a competent local who tells you the saddle is icy. Not a brand
selling a feeling.

Do:
- Name the age and the source of anything uncertain: "reported four days ago",
  "crowd-reported", "we do not verify them".
- Name where the data stops, by place name.
- Use plain numbers and 24h times.
- Keep Italian copy equally plain: "Non verifichiamo le condizioni."

Never:
- "safe route", "verified trail", "perfect for the whole family",
  "adventure awaits", "epic", "unlock", "seamless".
- Exclamation marks. Emoji. Any implied guarantee about conditions or safety.
- Softeners like "may be inaccurate" where a number would be honest.

Existing strings to replace on sight: any label promising accuracy, any
"Find me a trail" style placeholder (use "Speak, or type"), any error copy
that blames the user for a coverage gap.

---

## 9. Attribution and legal

- OpenStreetMap ODbL attribution must be visible on every view that renders
  map data or derived geometry. Do not move it into a modal or an about page.
- Trailforks attribution currently reads "terms pending". Keep that wording
  until commercial terms for consumer use are confirmed. Do not remove the
  Trailforks credit and do not upgrade the wording without being told.
- No copy anywhere may state or imply that a route is safe or verified.

---

## 10. Acceptance checklist

- [ ] tokens.css imported once globally; no hex literals in components.
- [ ] Ground is #0D0F0E on every screen; no light surfaces.
- [ ] Lime appears only on: route geometry, primary actions, confirmed data,
      active map layer, the wordmark, the first figure in a set.
- [ ] Flare appears only on: hazard, stale data, missing coverage.
- [ ] Every route card has a hazard block when hazards exist, and a Sources
      disclosure always.
- [ ] Sources shows OSM way IDs, Trailforks ID, MAPS_TO distance, and the
      never-merged wording.
- [ ] ODbL attribution visible wherever map data renders.
- [ ] All corners square except app icon and mic button; no shadows.
- [ ] Only two font families; labels 9.5px +0.11em uppercase; data in mono.
- [ ] No text sits on a lime or flare fill except ground-coloured button and
      badge labels.
- [ ] No banned copy from section 8 remains.
- [ ] Keyboard focus is visible on every interactive element (2px lime outline,
      2px offset).

---

## 11. Known gaps — ask, do not invent

- Photography is specified but not supplied: flat light, real conditions, hut
  interiors with people eating rather than posing. No drone hero shots, no
  summit arms-up. Use a --vv-panel block with a Label placeholder until real
  images exist.
- The OSM way IDs and Trailforks ID in the mockups are illustrative. Wire them
  to real values from the graph; do not ship the sample IDs.
- No light theme, no marketing site, and no onboarding flow are defined here.