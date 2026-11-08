// Star Ground's printed look: house-style's type on white paper, with the
// green accent darkened enough to read in print.

#let ink = rgb("#16181d")
#let muted = rgb("#5d6470")
#let faint = rgb("#9aa1ab")
#let rule = rgb("#d9dde3")
#let wash = rgb("#f4f6f8")
#let accent = rgb("#15803d")

#let sans = "DM Sans 14pt"
#let mono = "JetBrains Mono"

// A resistor band's colour, by name.
#let band-colours = (
  black: rgb("#1b1b1b"),
  brown: rgb("#7b4a26"),
  red: rgb("#d22b2b"),
  orange: rgb("#f07a12"),
  yellow: rgb("#f3cf1b"),
  green: rgb("#2f9e44"),
  blue: rgb("#2563eb"),
  violet: rgb("#7c3aed"),
  grey: rgb("#8b8f96"),
  white: rgb("#ffffff"),
  gold: rgb("#c8a13a"),
  silver: rgb("#b9bec5"),
)

// The document's base text settings.
#let base(body) = {
  set text(font: sans, size: 9.5pt, fill: ink, lang: "en")
  set par(leading: 0.55em)
  // DM Sans draws the ohm sign, not the Greek capital omega.
  show "Ω": "\u{2126}"
  body
}

// A small mono label, like house-style's eyebrows.
#let eyebrow(body, fill: muted) = text(
  font: mono,
  size: 7pt,
  tracking: 0.06em,
  fill: fill,
  upper(body),
)

// A resistor's colour bands, drawn as a small resistor body.
#let bands(names, height: 7pt) = {
  if names.len() == 0 { return }
  box(
    baseline: 1.5pt,
    fill: rgb("#e8d9b8"),
    radius: 2pt,
    inset: (x: 2.5pt),
    stroke: 0.4pt + rgb("#b9a57c"),
    stack(
      dir: ltr,
      spacing: 1.6pt,
      ..names.enumerate().map(((i, name)) => {
        let gap = if i == names.len() - 1 { 2.4pt } else { 0pt }
        h(gap)
        box(
          width: 2.2pt,
          height: height,
          fill: band-colours.at(name),
          stroke: if name == "white" { 0.3pt + faint } else { none },
        )
      }),
    ),
  )
}

// A part's value with what else identifies it.
#let part-cell(part, size: 9.5pt) = {
  text(size: size, weight: 500, part.text)
  if part.bands.len() > 0 {
    h(5pt)
    bands(part.bands)
  }
  if part.code != "" {
    h(5pt)
    box(
      inset: (x: 3pt, y: 1.5pt),
      radius: 2pt,
      stroke: 0.5pt + rule,
      text(font: mono, size: 7pt, fill: muted, part.code),
    )
  }
  if part.detail != "" {
    linebreak()
    text(size: 7.5pt, fill: muted, part.detail)
  }
}

// An empty box to tick.
#let tick-box = box(width: 8pt, height: 8pt, radius: 1.5pt, stroke: 0.7pt + muted)
