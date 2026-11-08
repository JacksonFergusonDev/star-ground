// The build book: a cover, the shopping list, and each project's checklist
// in the order its parts are soldered. Its contents come from
// `documents.build_book`, as JSON.

#import "theme.typ": *

#let book = json(bytes(sys.inputs.data))

#set document(title: "Build book", author: "Star Ground")
#set page(
  paper: "us-letter",
  margin: (x: 17mm, top: 20mm, bottom: 18mm),
  header: context {
    if counter(page).get().first() > 1 {
      set text(size: 7.5pt, fill: muted)
      grid(
        columns: (1fr, auto),
        eyebrow[Star Ground · Build book],
        eyebrow(book.generated),
      )
      v(-4pt)
      line(length: 100%, stroke: 0.4pt + rule)
    }
  },
  footer: context {
    set text(size: 7.5pt, fill: muted)
    grid(
      columns: (1fr, auto),
      eyebrow[Star Ground #book.version],
      eyebrow[Page #counter(page).display() of #counter(page).final().first()],
    )
  },
)

#show: base

#show heading.where(level: 1): it => {
  set text(size: 20pt, weight: 500, tracking: -0.01em)
  block(above: 0pt, below: 10pt, it.body)
}
#show heading.where(level: 2): it => {
  set text(size: 12pt, weight: 500)
  block(above: 16pt, below: 6pt, sticky: true, it.body)
}

// A table in the house manner: hairline rules, a mono header that repeats
// on every page, no boxes around cells.
#let ruled-table(columns: (), header: (), align: auto, ..rows) = table(
  columns: columns,
  align: align,
  stroke: (x, y) => (
    bottom: if y == 0 { 0.6pt + muted } else { 0.4pt + rule },
  ),
  inset: (x: 5pt, y: 5pt),
  table.header(..header.map(h => eyebrow(h))),
  ..rows,
)

#let number(n) = text(font: mono, size: 8.5pt, str(n))

// --- Cover ---

#v(18mm)
#eyebrow(fill: accent)[Star Ground]
#v(2pt)
#text(size: 34pt, weight: 500, tracking: -0.02em)[Build book]
#v(4pt)
#text(size: 12pt, fill: muted)[
  #book.projects.map(p => if p.count > 1 [#p.name × #p.count] else [#p.name]).join[ · ]
]

#v(12mm)
#grid(
  columns: (1fr,) * 4,
  column-gutter: 8pt,
  ..(
    ("Made", book.generated),
    ("Pedals", str(book.projects.map(p => p.count).sum(default: 0))),
    ("Lines to buy", str(book.lines_to_buy)),
    ("Parts to buy", str(book.pieces_to_buy)),
  ).map(((label, value)) => block(
    width: 100%,
    inset: (top: 7pt),
    stroke: (top: 0.6pt + ink),
    {
      eyebrow(label)
      v(3pt)
      text(size: 13pt, weight: 500, value)
    },
  ))
)

#v(12mm)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 14mm,
  {
    eyebrow[Contents]
    v(6pt)
    set text(size: 9.5pt)
    outline(title: none, depth: 1, indent: 0pt)
  },
  if book.files.len() > 0 {
    eyebrow[In this pack]
    v(6pt)
    for file in book.files {
      block(below: 7pt, {
        text(weight: 500, file.name)
        linebreak()
        text(size: 8.5pt, fill: muted, file.about)
      })
    }
  },
)

#place(bottom + left, text(size: 8pt, fill: muted)[
  Every part comes from the parts lists you gave Star Ground. Check anything
  marked for checking against its document before you buy.
])

// --- Shopping list ---

#pagebreak()
= Shopping list

#text(fill: muted)[
  What to buy for every build in this book, after your stock and any parts
  that come with a board. "Buy" includes spares where parts are cheap and easy
  to lose.
]

#for group in book.shopping [
  == #group.category
  #ruled-table(
    columns: (1.6fr, auto, auto, auto, 2.4fr),
    align: (left, right, right, right, left),
    header: ("Part", "Needed", "In stock", "Buy", "Notes"),
    ..group.rows.map(row => (
      part-cell(row.part),
      number(row.needed),
      number(row.in_stock),
      text(font: mono, size: 8.5pt, weight: "bold", str(row.buy)),
      text(size: 8pt, fill: muted, row.notes),
    )).flatten(),
  )
]

// --- Each project's checklist ---

#for project in book.projects [
  #pagebreak()
  #eyebrow(fill: accent)[Checklist]
  #v(6pt)
  = #project.name

  #text(fill: muted)[
    #if project.count > 1 [For each of #project.count pedals:] else [For one pedal:]
    #project.parts parts in build order, lowest first, so the board sits flat
    on the bench as each taller part goes in. Tick each line as it goes in.
  ]

  #for stage in project.stages [
    // A short stage stays on one page; a long one breaks, its header repeating.
    #block(breakable: stage.rows.len() > 6, {
      v(10pt)
      grid(
        columns: (auto, 1fr),
        column-gutter: 8pt,
        align: horizon,
        text(font: mono, size: 9pt, fill: accent, weight: "bold")[
          #if stage.number < 10 [0]#stage.number
        ],
        text(size: 12pt, weight: 500, stage.name),
      )
      if stage.hint != "" {
        v(2pt)
        text(size: 8.5pt, fill: muted, stage.hint)
      }
      v(4pt)
      ruled-table(
        columns: (auto, auto, 1.7fr, 2fr, 1.1fr),
        align: (center + horizon, right + horizon, left + horizon, left + horizon, left + horizon),
        header: ("", "Qty", "Part", "Where", "Which way"),
        ..stage.rows.map(row => (
          tick-box,
          number(row.qty),
          part-cell(row.part),
          text(font: mono, size: 8pt, row.refs),
          text(size: 8pt, fill: accent, row.orientation),
        )).flatten(),
      )
    })
  ]
]
