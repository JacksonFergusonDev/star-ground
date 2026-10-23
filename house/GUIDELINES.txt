# House style guidelines

The design and writing rules every site in the family follows: [jacksonferguson.me](https://jacksonferguson.me) and the project sites on its subdomains. The CSS and scripts here implement most of them. The rest are judgment calls a stylesheet can't make, so they're written down here, and each site's `AGENTS.md` points at this file.

A site may add its own rules, but it never contradicts these. When a rule here is wrong, change it here and release a new version, rather than working around it in one site.

## Color and type

- **Read the tokens, never raw values.** Colors, typefaces, and sizes come from `css/tokens.css`. Product sites and apps may load `css/product.css` after it for shared navy surfaces, then define their brand accent in a `:root` rule. A value a site needs that no token covers is a sign the token is missing.
- **Each site uses one primary accent.** `--accent` marks what the reader can act on and what's current. Cyan is the default; a project may set its own brand color. Accent tints and interaction states follow that token, and a site may adjust their tokens and foregrounds for contrast. Success, warning, and error colors report outcomes independently of the accent, with a label or icon carrying the meaning too. The `--prompt` green belongs to the terminal.
- **Dark first.** The palette is dark. A site may offer a light scheme by redefining the tokens it reads, but terminals, recordings, and install boxes stay dark in both.
- **Two typefaces.** DM Sans for prose and headings, JetBrains Mono for code, commands, labels, and section markers. Headings are weight 500 with tight tracking, never bold.
- **One type scale.** Each tier has one token (`--fs-h1` to `--fs-label`), and the tiers shrink together on phones. Components never set their own sizes.

## Page anatomy

- **Sections open with a numbered marker.** `<div class="hs-section-marker"><span>01 / ABOUT</span></div>`, then an H2 that says something the marker doesn't. Only top-level sections are numbered.
- **Card eyebrows are unnumbered labels** (`FEATURED PROJECT`), in mono at `--fs-label`.
- **Cards are panels with a line border** (`--panel`, `--line`, 5px radius), and are never nested inside cards. A card that links lightens its border on hover; it never lifts, glows, or casts a shadow.
- **A page that scrolls far has a grid behind its sections**: 80px lines in the line color, faint enough to read as texture.

## Navigation and icons

- **Every link that stands on its own carries an icon that says where it goes.** That covers buttons, text links outside a sentence, cards, and jump links. The icon comes after the label, in the text's color:

  | Icon             | Where the link goes                                       |
  | ---------------- | --------------------------------------------------------- |
  | `arrow-down`     | Further down the same page                                |
  | `arrow-up`       | Back up the same page                                     |
  | `arrow-right`    | The next page, or a closely related page on the same site |
  | `arrow-left`     | The previous page, or back to where the reader came from  |
  | `arrow-up-right` | Another site, or a page only loosely related to this one  |

- **Brand links carry their brand.** A link to GitHub, LinkedIn, Python, or email shows its `brand-*` icon before the label instead of an arrow. A link to jacksonferguson.me from a project site shows `icons/jacksonferguson.svg`.
- **A link inside a sentence takes no icon.** It reads as part of the prose.
- **Use only the icons in `icons/`.** Never a text arrow (`→`), an emoji, or another icon set. A site that can inline SVG inlines these files; any other site uses `css/icons.css`. A missing icon is added here first.
- **Icons are decoration.** Mark them `aria-hidden="true"`; the label carries the meaning.

## Terminals and recordings

- **Every terminal is an `.hs-terminal` window**, whether it holds a recording or a screenshot. A screenshot generated as an image draws the same window: the same bar, border, title color, and traffic-light dots.
- **A recording starts itself once most of it is in view**, loops, and never shows a play button over it first. Readers who asked for reduced motion get a still first frame and the play button instead.
- **The player loads only when a recording nears the viewport.** A page without a recording loads none of it.

## Motion and loading

- **The first screen is text.** Nothing heavy (an image, a recording, a canvas, a library) holds up the first paint. Load it after the page is up, or when the reader scrolls toward it.
- **Background motion is texture, not a centerpiece.** An animated field behind a hero uses dark, subdued shades of the site's accent, sits well below the text in contrast, and never competes with it.
- **Motion can be stopped.** Anything that moves on its own for more than a few seconds has a visible pause control, stops when it's off screen or the tab is hidden, and stays still for readers who asked for reduced motion.

## Writing

The goal is readability: neither the wording nor the formatting should be something the reader has to work through. Every site uses one calm, clear, precise voice, and each page is written for one audience.

- **Concrete over abstract.** Name the file, command, place, or number, and say what happens. No hype or unmeasured claims; state limits as plainly as strengths.
- **The answer first.** Reasons and exceptions follow it.
- **Don't overload a sentence.** A sentence can carry a supporting clause, but shouldn't ask the reader to hold several new things at once. Three or more parallel items read better as a list.
- **Formatting the reader doesn't notice.** Bold marks what a reader scans for, such as a label, a control to press, or a term where it's defined, never general emphasis. No em dashes, which agents overuse; a comma, colon, semicolon, parentheses, or a new sentence does the job.
- **Person follows the site:** "I" on a personal site, "you" in documentation.
- **Never hard-wrap Markdown prose.**

### Audiences

- **General:** hiring managers, coworkers, and anyone on a first look. Homepages, project cards, READMEs, and documentation landing pages. Assume nothing, say what it is and why it matters, and link onward for depth.
- **Users:** people getting something done. Steps and outcomes, with each term defined where it first appears.
- **Technical:** contributors and readers who want the mechanics. Denser, with internal names, but no harder to read.

## Documentation sites

Rules for project documentation built on a docs theme (Zensical or Material for MkDocs). The landing page is a house page and follows everything above; the inner pages keep the theme's layout with the house tokens, fonts, and terminal window.

- **Each navigation section serves one audience:** guides and reference for Users, internals and contributor pages for Technical readers.
- **Each rule is stated once,** on the page that owns it. Other pages link there.
- **Only top-level pages carry a navigation icon.** A page that sits directly in the navigation, outside any section, sets `icon:` in its front matter, and it shows beside the page in the left navigation. A page inside a section sets none. Pick icons that say what the page is for, and never repeat one.
- **Cards at the top of a page are optional.** Add them only when they genuinely help a reader choose where to go, or see a page's main points before the detail. A page that reads well without them has none.
- **Cards come in twos, fours, or sixes.** Each card has to earn its place: if only five say something worth saying, use four. Never pad a grid to reach an even count, and never repeat in a card what the heading under it already says.
- **Every page has a `description`** in its front matter.
- **Terminal output is shown, not described.** Use a recording or a generated screenshot in the house terminal window rather than pasting long output into a code block, and keep code blocks for what the reader types.

## Interaction

- **Focus is always visible:** a 2px `--accent` outline. Filled copy buttons use a contrasting inset outline from `--copy-focus`.
- **One install box per product**, with uv first.
- **Copy buttons confirm.** They say `Copied!` or `Failed` for a moment, then show their label again (`js/copy-command.js` does this).
