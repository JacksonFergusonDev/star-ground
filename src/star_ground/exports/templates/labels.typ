// Labels for Avery 5160 (and 8160) sheets: 30 to a US Letter page, three
// columns of ten. Its contents come from `documents.label_sheet`, as JSON.
//
// The sheet's geometry, from Avery's template: labels 2.625" by 1", a 0.5"
// top margin, a 0.1875" left margin, and 0.125" between columns.

#import "theme.typ": *

#let sheet = json(bytes(sys.inputs.data))

#let label-width = 2.625in
#let label-height = 1in
#let left-margin = 0.1875in
#let top-margin = 0.5in
#let column-pitch = 2.75in
#let per-page = 30

#set document(title: "Labels (Avery 5160)", author: "Star Ground")
#set page(paper: "us-letter", margin: 0pt)
#show: base

#let label(item) = box(
  width: label-width,
  height: label-height,
  inset: (x: 0.12in, y: 0.09in),
  radius: 4pt,
  // A faint outline, to cut along when printing on plain paper.
  stroke: 0.3pt + rule,
  {
    grid(
      columns: (1fr, auto),
      column-gutter: 4pt,
      box(height: 7pt, clip: true, eyebrow(item.project)),
      eyebrow(fill: accent, item.stage),
    )
    v(1fr)
    {
      text(size: 14pt, weight: 600, item.part.text)
      if item.part.bands.len() > 0 {
        h(6pt)
        bands(item.part.bands, height: 9pt)
      }
      if item.part.code != "" {
        h(6pt)
        text(font: mono, size: 8pt, fill: muted, item.part.code)
      }
    }
    if item.part.detail != "" {
      linebreak()
      text(size: 7pt, fill: muted, item.part.detail)
    }
    v(1fr)
    grid(
      columns: (1fr, auto),
      column-gutter: 6pt,
      box(height: 8pt, clip: true, text(font: mono, size: 6.5pt, fill: muted, item.refs)),
      text(font: mono, size: 6.5pt, weight: "bold")[× #item.qty],
    )
  },
)

#for (i, item) in sheet.labels.enumerate() {
  let slot = calc.rem(i, per-page)
  if i > 0 and slot == 0 { pagebreak() }
  let column = calc.rem(slot, 3)
  let row = calc.quo(slot, 3)
  place(
    top + left,
    dx: left-margin + column * column-pitch,
    dy: top-margin + row * label-height,
    label(item),
  )
}
