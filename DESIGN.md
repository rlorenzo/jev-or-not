---
name: Pilot Reference Docket
description: A clerk's minute book for entering, closing, and sealing John's rulings by ear.
colors:
  ledger-paper: "#f4f5f2"
  paper-sunk: "#eaece7"
  paper-raised: "#fbfbf9"
  hairline-rule: "#d6dad3"
  rule-strong: "#b3bab1"
  iron-gall-ink: "#1c2230"
  ink-lift: "#2a3245"
  ink-second: "#3f4757"
  faded-ink: "#5d6573"
  docket-green: "#2f5d50"
  docket-green-deep: "#264e43"
  docket-green-wash: "rgba(47, 93, 80, 0.11)"
  stamp-yes: "#2d6a4e"
  stamp-no: "#9a2b2b"
  stamp-ambiguous: "#85561a"
  stamp-no-ruling: "#5b6370"
typography:
  display:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(30px, 3.4vw, 44px)"
    fontWeight: 800
    lineHeight: 1.08
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  entry-number:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  title:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.4
  stamp:
    fontFamily: "Public Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "0.12em"
rounded:
  none: "0px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "18px"
  lg: "28px"
  gutter: "40px"
  gutter-compact: "16px"
components:
  button-primary:
    backgroundColor: "{colors.iron-gall-ink}"
    textColor: "{colors.ledger-paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "{colors.ink-lift}"
  button-green:
    backgroundColor: "{colors.docket-green}"
    textColor: "{colors.ledger-paper}"
    rounded: "{rounded.none}"
    padding: "9px 16px"
  button-green-hover:
    backgroundColor: "{colors.docket-green-deep}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.iron-gall-ink}"
    rounded: "{rounded.none}"
    padding: "9px 16px"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.stamp-no}"
    rounded: "{rounded.none}"
    padding: "9px 16px"
  mark-cta:
    backgroundColor: "transparent"
    textColor: "{colors.docket-green}"
    rounded: "{rounded.none}"
    padding: "8px 14px"
  mark-cta-hover:
    backgroundColor: "{colors.docket-green-wash}"
  play-button:
    backgroundColor: "{colors.iron-gall-ink}"
    textColor: "{colors.ledger-paper}"
    rounded: "{rounded.none}"
    size: "46px"
  segment:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink-second}"
    rounded: "{rounded.none}"
    padding: "5px 9px"
  segment-pressed:
    backgroundColor: "{colors.iron-gall-ink}"
    textColor: "{colors.ledger-paper}"
  field-underline:
    backgroundColor: "transparent"
    textColor: "{colors.iron-gall-ink}"
    rounded: "{rounded.none}"
    padding: "4px 0 5px"
  verdict-stamp:
    backgroundColor: "transparent"
    typography: "{typography.stamp}"
    rounded: "{rounded.none}"
    padding: "5px 12px 4px"
  docket-line-active:
    backgroundColor: "{colors.docket-green-wash}"
    padding: "16px 0"
  session-rail:
    backgroundColor: "{colors.paper-sunk}"
    width: "264px"
---

<!-- markdownlint-configure-file {"MD025": {"front_matter_title": ""}, "MD036": false} -->

# Design System: Pilot Reference Docket

## Overview

**Creative North Star: "The Clerk's Docket"**

John is the court and the tool is the clerk's minute book. Every surface is ledger paper ruled with hairlines and written in navy iron-gall ink. An episode is a session, a ruling is a numbered docket entry, and a verdict is a rubber stamp pressed onto its line. Structure comes from rules (hairlines, a 2px ink head-rule over each working block) instead of boxes. There are no cards, no rounded corners, and no drop shadows.

The density is a working ledger's: one slim sunk rail listing ten sessions, and a main column of full-width ruled lines on a 40px gutter. Docket green is the only working accent, reserved for progress, focus, the marking affordance, and closed/sealed state. The four stamp inks carry verdict meaning and nothing else. Public Sans is the only face, set with tabular figures throughout so timecodes and entry numbers stay aligned like a ledger column.

The one signature motion is the stamp press. When a verdict lands, the stamp strikes (scale 1.55 to 0.96 to 1, blur clearing to sharp) and the entry number inks in from a blur. Everything else is still.

**Key Characteristics:**

- Cool ledger paper, navy ink, one green accent, four verdict stamp inks.
- Square corners everywhere (0px); hairline rules instead of containers.
- Public Sans only, tabular figures on by default.
- Outlined uppercase verdict stamps set slightly off true, multiply-blended into the paper.
- Keyboard-first: every control shows its key as a small ledger keycap.

## Colors

A near-monochrome ledger of cool paper and navy ink, with one green working accent and a closed set of stamp inks that only ever mean a verdict.

### Primary

- **Docket Green** (docket-green): the single working accent. Tally marks for closed sessions, the "Mark ruling" outline action, focus rings, caret, closed/sealed status marks, links, and JOHN's speaker label in the clerk's transcript. Deepens to docket-green-deep on hover of filled green buttons.
- **Docket Green Wash** (docket-green-wash): the tint for the active or being-written docket line, the current transcript line, and the marking-CTA hover. Its strong siblings (22% for text selection, 7% to 16% for waveform entry spans) are the same green at different opacities.

### Secondary (verdict stamp inks)

- **Stamp Yes** (stamp-yes): a leafier green than the docket accent, used only on YES stamps.
- **Stamp No, Oxblood** (stamp-no): NO stamps, and double duty as the danger color for destructive actions, error text, and failure toasts.
- **Stamp Ambiguous, Umber** (stamp-ambiguous): AMBIGUOUS stamps only.
- **Stamp No Ruling, Slate** (stamp-no-ruling): NO RULING stamps, always with a dashed border.

### Neutral

- **Ledger Paper** (ledger-paper): the page, and reversed text on ink fills.
- **Sunk Paper** (paper-sunk): the session rail, one step darker than the page.
- **Raised Paper** (paper-raised): the face of keycaps, transport buttons, segmented controls, selects, and inline confirms.
- **Hairline Rule** (hairline-rule): every divider between docket lines, rail rows, tabs, and waveform ruling.
- **Strong Rule** (rule-strong): control borders, field underlines, minute ticks on the waveform, scrollbar thumbs.
- **Iron-Gall Ink** (iron-gall-ink): all primary text, the 2px head-rule over the bench and extraction runs, the play button, primary buttons, and the pressed state of segments. Lifts to ink-lift on hover.
- **Second Ink** (ink-second): secondary text inside controls and body copy in empty and locked states.
- **Faded Ink** (faded-ink): metadata, timestamps, field labels, key hints, the italic "In re:" prefix.

### Named Rules (Colors)

**The One Accent Rule.** Docket green is the only non-verdict color. Progress, focus, and the marking action share it; nothing decorative does.

**The Stamp Ink Rule.** The four stamp inks mean yes, no, ambiguous, and no ruling. They never color anything else, except oxblood also serving as danger.

## Typography

**Display Font:** Public Sans (vendored variable woff2, weight 100 to 900, with ui-sans-serif and system-ui fallback)
**Body Font:** Public Sans
**Label/Mono Font:** none; figures come from Public Sans with `font-variant-numeric: tabular-nums` set on the body.

**Character:** One sober civic grotesque doing every job, from a heavy 800 case caption down to 11px keycaps. Hierarchy comes from weight and size, not from a second face.

### Hierarchy

- **Display** (800, clamp(30px, 3.4vw, 44px), 1.08, -0.025em): the session caption, "In re: Subject". The "In re:" prefix is 400 italic in faded ink.
- **Headline** (700, 34px, 1, -0.02em, tabular): the transport clock. Duration trails at 15px/500 in faded ink.
- **Entry Number** (800, 22px, -0.02em, tabular): the "#k" at the head of each docket line.
- **Title** (600, 17px, 1.3): the subject of each docket entry and scorecard row. Rail session titles run 600 at 14px.
- **Body** (400, 15px, 1.5): base text; empty and locked explanations cap at 58 to 60ch, transcripts at 82ch.
- **Label** (600, 12px): field labels and metadata. Controls and buttons use 600 to 700 at 13 to 14px.
- **Stamp** (800, 19px, 0.12em, uppercase): verdict stamps, with a 10px/700/0.18em strength line beneath. The small stamp variant sets 13px.

### Named Rules (Typography)

**The Ledger Column Rule.** Tabular figures are on everywhere. Timecodes, entry numbers, counts, and the clock must align as a column.

**The Stamp Is the Loudest Mark Rule.** On a docket line, the verdict stamp outweighs everything except the session caption. Nothing else on the line is uppercase and heavily tracked.

## Layout

A two-column frame: a 264px sticky session rail (full viewport height, sunk paper, right hairline) beside a fluid main column. The main column stacks a slim "next" status bar (42px min), the session caption, the bench (transport, a 132px waveform strip ruled like a ledger margin, and a key legend), tabs, and the docket. Everything in main sits on a 40px side gutter.

Docket lines are a four-column grid: entry number (58px), time span (150px), subject (fluid), stamp (210px, right-aligned), with 18px gaps and 16px vertical padding, separated by hairlines. A line being written expands in place with a 2px ink top rule, an ink bottom rule, and the green wash; there is no side panel.

Spacing moves in small steps: 6px inside control groups, 10px between related fields, 18px between columns and transport groups, 28px between tabs and before session footers, 40px gutters.

At 1100px the line columns tighten (48 / 128 / fluid / 170) and form fields stack. At 820px the rail becomes a horizontal scrolling strip of session cards above main, the rail footer hides, gutters drop to 16px, and docket lines collapse to number plus span, with subject and stamp on full-width rows below. On touch-only devices the key legend hides.

## Elevation & Depth

Flat. The system has no drop shadows. Depth is tonal and ruled: sunk paper for the rail, raised paper for control faces, hairlines between entries, and a 2px ink head-rule to open a working block. The current rail row is marked by switching to page paper between two inset hairlines. Stamps sit into the paper with `mix-blend-mode: multiply` rather than floating above it. The only blur in the system is transient, inside the stamp press and entry inking animations.

### Named Rules (Elevation & Depth)

**The Ruled Not Raised Rule.** Separate things with a rule or a paper tone, never with a shadow or a card.

## Shapes

Every corner is square (0px): buttons, fields, keycaps, tally marks, the "next" dot, the play button, the toast. Form language is the ledger's: 1px hairlines, 1.5px underlines for text fields, 2px ink rules for block heads and the selected tab. The only off-axis shape is the verdict stamp, rotated by a small per-entry tilt from a fixed set (-1, -1.6, -0.5, -1.3, 0.4, -0.8 degrees) so no two neighbouring stamps sit alike. Keycaps carry a 2px bottom border to read as physical keys.

## Components

### Buttons

Plain, square, and ink-first.

- **Shape:** square (0px), 1px border in the fill color.
- **Primary:** iron-gall ink fill, ledger paper text, 700 at 14px, 9px 16px padding, trailing keycap in current color. Hover lifts to ink-lift.
- **Green:** docket green fill, used only for closing a session and sealing the record once ready.
- **Quiet:** transparent with a strong-rule border and ink text; hover darkens the border to ink.
- **Danger:** transparent, oxblood text, 45% oxblood border; hover sets the full border and a 6% oxblood tint.
- **Disabled:** 45% opacity, not-allowed cursor.
- **Mark ruling CTA:** outlined in docket green with green text and "[ ]" keycaps; hover fills with the green wash.
- **Play:** a 46px solid ink square with an inline SVG play/pause glyph.

### Segmented controls and transport buttons

- **Style:** raised paper face, strong-rule border, 600 at 13px in second ink. Segments collapse borders (-1px overlap) into one row, each option carrying its key.
- **State:** pressed segments and toggles invert to an ink fill with paper text. Hover darkens the border. Disabled drops to 40 to 45% opacity.

### Inputs / Fields

- **Style:** no box. Transparent ground, a 1.5px strong-rule underline, 16px text (subject at 17px/600). Labels sit above at 12px/600 in faded ink.
- **Focus:** the underline turns docket green; no outline ring on text fields. All other focusable elements take a 2px green outline at 2px offset.
- **Selects:** raised paper with a strong-rule border.

### Navigation

- **Session rail:** sunk paper. The head holds the docket title (800, 19px) and a ten-segment tally (5px bars, green when closed). Rows are a grid of session number, title with meta, and a status mark (open ring, closed check disc, sealed seal, all inline SVG). The current row shows page paper between inset hairlines, with the number and title at 800 ink.
- **Tabs:** text tabs 28px apart on a hairline. The selected tab is ink with a 2px ink underline. Locked tabs carry a small lock icon.

### Verdict Stamp (signature)

An outlined uppercase stamp, 2.5px border in current color, 800 at 19px, 0.12em tracking, with an optional strength line beneath. It is rotated by its entry's tilt and multiply-blended. YES, NO, and AMBIGUOUS are solid-bordered; NO RULING and the unstamped placeholder are dashed. On entry it plays the press: 420ms on the expo ease-out (cubic-bezier(0.16, 1, 0.3, 1)), from scale 1.55 with a 3px blur to 0.96 and then to rest. The entry number inks in over 520ms at the same time. Both are removed under reduced motion.

### Docket Line (signature)

A ruled ledger row: entry number, clickable time span (underlined in strong rule), subject with meta, and the stamp at the right. Hover shows a faint ink tint that fades out at both ends; the active line takes the green wash. A superseded line strikes through its number and subject in faded ink.

### Keycap

Inline key hints: 600 at 11px, raised paper, strong-rule border with a 2px bottom edge, square. Inside filled buttons they go transparent and take the current color.

### Toast

An ink bar with paper text, 600 at 14px, fixed bottom-left of the main column. It rises 12px and fades in over 260ms; oxblood variant for failures.

## Do's and Don'ts

### Do

- **Do** separate content with hairline rules (1px hairline-rule) and open working blocks with a 2px iron-gall ink rule.
- **Do** keep every corner square (0px).
- **Do** set all numbers in Public Sans with tabular figures.
- **Do** reserve docket green for progress, focus, the marking action, and closed or sealed state.
- **Do** express verdicts only as outlined uppercase stamps in their own ink, rotated by a small per-entry tilt and multiply-blended.
- **Do** show the key for every keyboard action as a square keycap beside the control.
- **Do** keep motion to the stamp press, entry inking, and the toast, and drop all three under reduced motion.

### Don't

- **Don't** use cards, rounded corners, or drop shadows; depth comes from paper tone and rules.
- **Don't** use stamp inks for anything but verdicts (oxblood may also mark danger).
- **Don't** introduce a second typeface.
- **Don't** fill a verdict stamp solid; stamps are outlined ink on paper.
