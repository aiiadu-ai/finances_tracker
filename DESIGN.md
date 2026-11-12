# Anastasiia Ukrainska — Design System

**Nature × technology.** A calm, muted, natural palette paired with precise, technical typography. One focus per screen, plenty of air, and a soft warm glow as the accent. Light and dark themes are equal citizens.

Use this for everything made for me: slides, documents, designs, web pages, dashboards, app screens, social visuals.

---

## 1. Principles

1. **One focus per screen.** One main headline, number or image, plus an obvious next step. Everything else is quieter.
2. **Meaning over mood.** The headline or image must say what this is about at a glance. No "movie poster" atmosphere without content — if a viewer can't tell what it is, they won't read the text.
3. **Nature × technology.** A living object (water, stone, plants, light, fabric) framed by technical elements: hairline callouts, monospace labels, a dot grid.
4. **Air, not decoration.** Fewer words and elements per screen. No ornament that doesn't carry meaning.
5. **Contrast, always.** Text is readable in both themes (WCAG ≥ 4.5:1). Muted colors are for surfaces, never for text.
6. **Varied visuals.** Mix types — photo, diagram with callouts, big number, glow. Never 5–10 similar photos in a row.

---

## 2. Color

### 2.1 Tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `surface` | `#F2EEE8` | `#1B2327` | Page / slide / document background. Light = muted beige, dark = graphite blue (not chocolate). |
| `surface-raised` | `#FBF9F5` | `#242D32` | Cards, panels, inputs on top of `surface`. |
| `surface-sunken` | `#E6E0D6` | `#151B1E` | Recessed areas: code, tables, secondary blocks. |
| `ink` | `#2B2422` | `#EDE6DC` | Main text and headings. Light = cool dark brown. ≥ 11:1 on all surfaces. |
| `ink-muted` | `#5E5652` | `#B3ACA3` | Secondary text, captions, labels. ≥ 5.4:1. Never use a lighter grey for text. |
| `line` | `#D3CABE` | `#33404A` | Decorative hairlines, dividers, card outlines. Not for control borders. |
| `line-strong` | `#8A8079` | `#7D8C93` | Input and outline-button borders, meaningful callout lines. ≥ 3:1. |
| `accent` | `#6E1F2E` | `#9A4553` | Burgundy / plum. Fill of the one primary button, active state, the one highlighted number. **Accent only — never large areas.** |
| `on-accent` | `#F7F1EA` | `#FFF5EF` | Text/icons on `accent` (9.9:1 light, 5.8:1 dark). |
| `accent-ink` | `#6E1F2E` | `#E3A8B1` | Burgundy as text: links, highlighted words, accent numbers (≥ 7:1). |
| `accent-soft` | `#EBD9D9` | `#3A2A30` | Background for burgundy chips, selected rows, hover. Text on it: `accent-ink` or `ink`. |
| `slate` | `#6D8C8F` | `#5F7F84` | Grey-blue ("ocean"). Blocks, charts, illustrative areas. Not for text on light. |
| `slate-ink` | `#3F5A5E` | `#9EC0C6` | Grey-blue as text: technical labels, second accent in charts (≥ 5.6:1). |
| `mist` | `#D6E4E8` | `#2A3A40` | Pale blue mist: info-block backgrounds, calm areas. Text on it: `ink`. |
| `sage` | `#8C987C` | `#7E8A6E` | Sage green. Third chart series, natural accents. Not for text. |
| `sand` | `#CDBFA8` | `#8A7E6C` | Sand. Background areas, photo backdrops, fourth chart series. |
| `cocoa` | `#684F48` | `#A08A82` | Warm brown. Fifth chart series, small details. Not for large areas. |
| `glow-core` | `#F1D9C4` | `#C98A73` | Center of the warm glow. Glow only. |
| `glow-edge` | `#B5606B` | `#6E1F2E` | Edge of the warm glow, fading into `surface`. Glow only. |
| `success` | `#2E6A63` | `#7FC4B8` | Success. Always with a word or ✓. Blue-green so it never reads as burgundy. |
| `warning` | `#8A5A1C` | `#E0B27A` | Warning. Always with a word or !. |
| `danger` | `#A3361F` | `#F0A08C` | Error / destructive action. Rust, distinct from burgundy `accent`. Always with a word or ✗. |
| `focus` | `#3F5A5E` | `#9EC0C6` | Focus ring: 2px solid, 2px offset. ≥ 3:1 on all surfaces. |

### 2.2 Rules

- **Text:** `ink` for primary, `ink-muted` for secondary. Nothing lighter.
- **Accent:** one burgundy fill per screen (primary button or key number). Burgundy never floods a background.
- **Chart series order:** `accent` → `slate` → `sage` → `sand` → `cocoa`. `mist` for info backgrounds.
- **Beige is never the whole mood.** Next to `surface`/`sand` there is always a deep color (`ink`, `accent` or a dark block). Otherwise it turns into a "nursery".
- **Dark theme is graphite blue,** not chocolate. Dark brown is for text and details only.
- **Status colors** always come with a word or symbol (✓ ! ✗), never color alone.
- **No** orange/yellow, neon, purple-to-blue gradients, olive.

### 2.3 Warm glow

A large blurred shape behind the main headline or number: `glow-edge` → `glow-core`, fading into `surface`. One per screen. It should make the screen feel warm and invite scrolling — not compete with the text.

```css
.glow {
  position: absolute; width: 520px; height: 520px; border-radius: 50%;
  background: radial-gradient(circle at 50% 60%, var(--glow-core) 0%, var(--glow-edge) 45%, transparent 72%);
  filter: blur(60px); opacity: .85; pointer-events: none; z-index: 0;
}
```

---

## 3. Typography

### 3.1 Families

- **Onest** — headings and body. Even stroke width, excellent Cyrillic.
- **IBM Plex Mono** — labels, numbers, codes, technical tags.

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@300..700&family=IBM+Plex+Mono:wght@400;500&display=swap">
```

```css
--font-sans: "Onest", "Segoe UI", system-ui, sans-serif;
--font-mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
```

### 3.2 Scale

| Style | Family | Size / line | Weight | Extra | Use |
|---|---|---|---|---|---|
| `display-xl` | Onest | 72 / 0.95 | 300 | -0.02em | One main headline per screen. **Light background only.** |
| `display-xl-dark` | Onest | 72 / 0.95 | 400 | -0.02em | Same on dark backgrounds (min 400). |
| `h1` | Onest | 40 / 44 | 500 | -0.01em | Page / slide / document title. |
| `h2` | Onest | 28 / 34 | 500 | | Section. |
| `h3` | Onest | 20 / 28 | 600 | | Subsection, card title. |
| `lead` | Onest | 20 / 30 | 400 | | One-two sentence lead under the main headline. |
| `body` | Onest | 16 / 24 | 400 | | Body text, max ~65 characters per line. |
| `body-sm` | Onest | 14 / 20 | 400 | | Secondary text, card descriptions. |
| `label` | Plex Mono | 12 / 16 | 500 | 0.06em, UPPERCASE | Technical labels in square brackets: `[CASE-01]`, `[MORNING]`, `[+] ADD`. Color `ink-muted` or `slate-ink`. |
| `data` | Plex Mono | 14 / 20 | 400 | tabular nums | Numbers, codes, table values. |
| `data-xl` | Plex Mono | 40 / 44 | 400 | tabular nums | The one key number of a block. |

### 3.3 Rules

- **Thin weights are allowed** for large headlines on light backgrounds.
- **Not allowed:** very thin weights (100–200) on dark backgrounds; text under 14px thinner than 400 on dark.
- **Forbidden:** script/handwritten fonts; high-contrast serifs (thick + thin strokes, "calligraphic" look); luxury-boutique serif caps with wide tracking; Inter/Roboto as the default "safe" choice.
- Headings use `text-wrap: balance`.
- On dark backgrounds, text is slightly heavier and larger than on light.

---

## 4. Space, shape, depth

### 4.1 Spacing (4px grid)

| Token | Value | Use |
|---|---|---|
| `space-1` | 4px | Icon ↔ text, inside chips |
| `space-2` | 8px | Label ↔ value, small gaps |
| `space-3` | 12px | Vertical padding of buttons and chips |
| `space-4` | 16px | Minimum side gutter, small card padding, dot-grid pitch |
| `space-6` | 24px | Card padding, gap between cards |
| `space-8` | 32px | Between blocks inside a section |
| `space-12` | 48px | Between sections |
| `space-16` | 64px | Slide margins, air around the main headline |

### 4.2 Radius

| Token | Value | Use |
|---|---|---|
| `radius-sm` | 8px | Inputs, thumbnails, code blocks |
| `radius-md` | 16px | Cards, panels |
| `radius-lg` | 28px | Large blocks, photos, "cushion" sections (soft, generous rounding) |
| `radius-pill` | 999px | **Buttons and chips — always pills** |

### 4.3 Strokes and shadow

- `stroke-hairline` = 1px — dividers, callouts, outlines. `line` for decorative, `line-strong` for controls and meaningful callouts.
- `stroke-focus` = 2px — focus ring in `focus`.
- `shadow-soft` — the only shadow, for raised cards and light buttons. Not on every block.
  - Light: `0 1px 2px rgba(43,36,34,.06), 0 8px 24px rgba(43,36,34,.08)`
  - Dark: `0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35)`

---

## 5. Components

### 5.1 Button (pill)

- **Primary:** `accent` fill, `on-accent` text. One per screen.
- **Outline:** transparent, 1px `line-strong` border, `ink` text. Secondary action.
- **Light:** `surface-raised` + `shadow-soft`. For buttons over photos or glow.
- Shape `radius-pill`; padding `space-3` × `space-6`; Onest 500 15px. Arrow `→` in mono. Label is a verb: "Send", "Save".
- Don't: two burgundy buttons side by side, square buttons, gradients.

```css
.btn { font: 500 15px/20px var(--font-sans); border-radius: var(--radius-pill); padding: var(--space-3) var(--space-6); border: 1px solid transparent; display: inline-flex; gap: var(--space-2); align-items: center; }
.btn:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.btn-primary { background: var(--accent); color: var(--on-accent); }
.btn-outline { background: transparent; color: var(--ink); border-color: var(--line-strong); }
.btn-light { background: var(--surface-raised); color: var(--ink); box-shadow: var(--shadow-soft); }
```

### 5.2 TicketCard

A "ticket" card with semicircle notches on both sides and monospace labels — for cases, products, process steps.

- `surface-raised`, `radius-md`, `shadow-soft`; notches in the color of the background under the card.
- Top: mono code `label` in `slate-ink` (`[CASE-01]`).
- Title: mono, uppercase, 24px.
- Meta row: two `label`s at opposite edges (`[WEEKLY]` · `FRI 10:00`), `ink-muted`.
- Slider line: 1px `line-strong` with an `accent` dot.
- Description `body-sm`, 1–3 sentences. Tags mono, separated by ` / `.
- Bottom: one action, mono uppercase `[+] …` in `accent-ink`.
- In a row: 2–4 cards, equal height, different content.

### 5.3 Annotation (technical callout)

The "nature × technology" signature: a thin elbow line from a point on an object to a mono label, like a technical drawing.

- Line: 1px `line-strong`, horizontal and vertical segments only.
- Anchor: 8px circle in `accent`.
- Label: either a box (`surface-raised`, `line-strong` border, `radius-sm`, padding `space-3`) with a `label` in `slate-ink` + one `body-sm` sentence, or just a `label` in `ink-muted`.
- 2–4 callouts per object. Lines never cross each other or run over text.
- The object: a nature photo/shape (stone, plant, water) or a product diagram.

### 5.4 Chips / tags

Pill, `radius-pill`, padding `space-1` × `space-3`, `label` style. Default: 1px `line` border, `ink-muted` text. Selected: `accent-soft` background, `accent-ink` text.

### 5.5 Inputs

`surface-raised`, 1px `line-strong` border, `radius-sm`, padding `space-3`. Label above in `label` style. Focus ring as above.

---

## 6. Layout

- One clear hierarchy: main headline/number → explanation → action.
- Generous whitespace; content groups use flex/grid `gap`, not margins.
- Light list on the left → detail card on the right works well for catalogs (choose → see details).
- Large soft-rounded blocks alternate with clean text sections.
- Slides: margins `space-16`, one idea per slide, the headline states the takeaway.
- Dashboards: summary before detail; state shown by form (chip, ✓/✗), not color alone.

---

## 7. Imagery

- Natural light and texture: water reflections, stone, plants, fabric. One subject in focus.
- "Nature in a technical frame": living object + hairline callouts, mono labels, dot grid.
- Mix visual types on a page; never a wall of similar photos.
- Text on photos only where the background is calm and contrast ≥ 4.5:1. Otherwise put text on `surface` next to the photo. Never half-overlapping text on busy imagery.
- Avoid: stock "team at laptop", mannequin people, 3D blobs, teddy bears, clouds, balloons, gold frames, foggy-mountain "premium studio" shots, dark fashion photography.

---

## 8. Content & tone

- Short, direct, no corporate phrasing. Active verbs on controls ("Send"), result in the past tense after ("Sent").
- Interface language follows the user (Ukrainian / English). **No surzhyk** — one message, one clean language.
- Emoji only to express emotion, never as section markers. Bullets: ▪ → ✓ ✗. Section dividers in headings may use 🟩 🟨 🟦 ✅ ⏭.

---

## 9. Never do

- Light on light, grey on grey, thin text on dark.
- A mash of small blocks with no hierarchy; everything competing for attention.
- Template "luxury": thin serif caps + foggy mountains; dark fashion photos on brown.
- Cute/plush pastel with no deep color.
- Script fonts, high-contrast serifs.
- Purple-blue gradients, cards with a colored left border, emoji on every line, orange/yellow glows.
- Cinematic visuals that don't explain what the thing is.

---

## 10. References (what I liked / didn't)

**Yes**
- *Sutéra "Reality by Design"* — nature inside technical frames, hairline callouts, mono labels, big dense headline with lots of air.
- *eeios DAWN/FLOW cards* — monospace type, `[MORNING]` labels, ticket-shaped cards, slider line, muted bottles on warm light grey.
- *diáfano* — clean, airy, large soft radii, wide-tracked headings, light pill buttons, clear block rhythm (but not its bright blue).
- *Warm glow onboarding* — almost no details, text + soft warm blurred light; the warmth makes you want to keep scrolling (technique only, recolored to burgundy → beige).
- *Hand with bottle in water* — natural light, texture, one subject, instantly clear.
- *ReliefMind* (partly) — one hero image + big readable headline, glassy pill chips.
- *BRUMA* (partly) — dark atmospheric base with warm light in the darkness, little text, clear blocks (but its thin high-contrast type is unreadable).

**No**
- *WaterPod* — light on light, low contrast, cluttered, only the black buttons are readable.
- *EVORA* — generic studio site, too much small text, nothing to hook onto.
- *Pequeño Sol* — plush nursery style, all pastel, script fonts.
- *ÉCLAT* — too much at once, poor layout/UX, every image the same.
- *Aubade Pages* — script fonts, gold frames, decoration for its own sake.
- *Eleanor* — right colors, but a mash of photos and identical blocks.
- *Tokyo trip* — script "Welcome", small grey text, no hierarchy.

**Palette sources:** Ivory Clay `#BFB692`, Creamy Oat `#F9E7C7`, Cocoa Rose `#684F48`, Schokobraun `#34211B`, Golden Sand `#88623E`, Silverveil `#93A8AD`, Smoky Sky `#919EA6`, Obsidian Mist `#1E2E34`, Ocean Whisper `#6D8C8F`, Winter Mist `#D6E9ED`, Hellblau `#A0BEDA`, Sage, Plum, Sand, Ivory. Excluded: Olive, Aged Sage.

---

## 11. CSS tokens (copy-paste)

```css
:root {
  --surface:#F2EEE8; --surface-raised:#FBF9F5; --surface-sunken:#E6E0D6;
  --ink:#2B2422; --ink-muted:#5E5652; --line:#D3CABE; --line-strong:#8A8079;
  --accent:#6E1F2E; --on-accent:#F7F1EA; --accent-ink:#6E1F2E; --accent-soft:#EBD9D9;
  --slate:#6D8C8F; --slate-ink:#3F5A5E; --mist:#D6E4E8; --sage:#8C987C; --sand:#CDBFA8; --cocoa:#684F48;
  --glow-core:#F1D9C4; --glow-edge:#B5606B;
  --success:#2E6A63; --warning:#8A5A1C; --danger:#A3361F; --focus:#3F5A5E;
  --shadow-soft:0 1px 2px rgba(43,36,34,.06), 0 8px 24px rgba(43,36,34,.08);
  --font-sans:"Onest","Segoe UI",system-ui,sans-serif;
  --font-mono:"IBM Plex Mono",ui-monospace,"Cascadia Mono",Consolas,monospace;
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-6:24px; --space-8:32px; --space-12:48px; --space-16:64px;
  --radius-sm:8px; --radius-md:16px; --radius-lg:28px; --radius-pill:999px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --surface:#1B2327; --surface-raised:#242D32; --surface-sunken:#151B1E;
    --ink:#EDE6DC; --ink-muted:#B3ACA3; --line:#33404A; --line-strong:#7D8C93;
    --accent:#9A4553; --on-accent:#FFF5EF; --accent-ink:#E3A8B1; --accent-soft:#3A2A30;
    --slate:#5F7F84; --slate-ink:#9EC0C6; --mist:#2A3A40; --sage:#7E8A6E; --sand:#8A7E6C; --cocoa:#A08A82;
    --glow-core:#C98A73; --glow-edge:#6E1F2E;
    --success:#7FC4B8; --warning:#E0B27A; --danger:#F0A08C; --focus:#9EC0C6;
    --shadow-soft:0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --surface:#1B2327; --surface-raised:#242D32; --surface-sunken:#151B1E;
  --ink:#EDE6DC; --ink-muted:#B3ACA3; --line:#33404A; --line-strong:#7D8C93;
  --accent:#9A4553; --on-accent:#FFF5EF; --accent-ink:#E3A8B1; --accent-soft:#3A2A30;
  --slate:#5F7F84; --slate-ink:#9EC0C6; --mist:#2A3A40; --sage:#7E8A6E; --sand:#8A7E6C; --cocoa:#A08A82;
  --glow-core:#C98A73; --glow-edge:#6E1F2E;
  --success:#7FC4B8; --warning:#E0B27A; --danger:#F0A08C; --focus:#9EC0C6;
  --shadow-soft:0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
  color-scheme: dark;
}
body { background: var(--surface); color: var(--ink); font: 400 16px/24px var(--font-sans); }
```
