# Star Ground rebuild plan

The plan for replacing the Streamlit app with a redesigned Star Ground on FastAPI, Astro and Svelte, and launching it on builder forums. It is the source of truth across sessions: read it before starting work on the rebuild, and update the [status](#status) in the same pull request that finishes a step.

Decided 2026-10-22 to 2026-10-24. A decision here changes only by editing this file, with the reason.

## Goals

1. **A polished, professional app** in the shared house style of [jacksonferguson.me](https://jacksonferguson.me) and [Protostar](https://protostar.jacksonferguson.me), consumed from [house-style](https://github.com/JacksonFergusonDev/house-style). Streamlit can't follow that style and feels like a prototype.
1. **A redesign, not a port.** Star Ground becomes a build workspace instead of a one-shot form, and makes its "never silently wrong" promise visible: every part and warning traceable to the spot in the document it came from.
1. **A good first impression at launch.** The forum launch waits for the new app, so feedback is about the app people will keep using.

## Decisions

### Stack

- **API:** FastAPI over `src/api.py`, with Pydantic models generated from its dataclasses. The API is stateless: every request carries what it needs, and the browser holds the user's state.
- **Frontend:** an Astro site (landing page, catalog pages) with the app as a Svelte 5 island, in `web/`.
  - Astro matches jacksonferguson.me, so house-style, linting (ESLint, Stylelint, Prettier) and browser tests (Playwright with axe) work the same way.
  - Svelte over React: scoped plain CSS reads the house tokens directly, and a stateful workspace takes less code. React's larger ecosystem only matters for live collaboration, a native mobile app, or React-only vendor kits, none of which are planned. If one is ever needed, Astro can host a React island beside the Svelte ones.
  - No Tailwind and no component kit: house-style tokens and components only.
  - **The site matches Protostar's landing page** (decided 2026-10-25): house-style's near-black palette with Star Ground's green accent, and the shared project-site shell from house-style v2 (header bar, hero field, jump links, section grid, footer, with a publisher note in the footer). house-style v2 split itself into parts so each site takes only what it uses; the website installs it with npm, so the repository no longer vendors a copy in `house/`.
  - **The hero shows a real circuit** (decided 2026-10-25): a Big Muff π laid out as a single-sided board with a star ground, its signal path and ground returns animated in raw WebGL2. It replaces house-style's attractor field and three.js on Star Ground's site; a generated board read as fake because its connections meant nothing.
- **Types:** the API's OpenAPI schema generates the frontend's TypeScript types (`@hey-api/openapi-ts`), so the two can't drift.
- **Rejected:** NiceGUI and Reflex (Python UI frameworks that keep Streamlit's server-driven model and fight custom styling), a Vite SPA without a site framework (no static pages for the landing page and catalog), and a downloadable desktop app (Python, onnxruntime and OpenCV per platform, code signing and auto-update, for little gain over hosting at about $0).

### Hosting and cost

- **Frontend:** GitHub Pages at `star-ground.jacksonferguson.me`. Free, served from a CDN, and never waits on the backend.
- **API:** Google Cloud Run, scaling to zero, 2 GiB and 2 vCPU per instance, max instances capped (the hard cost ceiling), with a budget alert.
- **Expected cost:** about $0 a month. Measured on an M3: a text PDF takes about 0.25 s and 160 MB; an OCR document takes 4 to 10 s, about 16 CPU-seconds and a 1.2 to 1.4 GB peak. Cloud Run's free tier (180k vCPU-seconds, 360k GiB-seconds a month) covers about 6,000 OCR documents a month.
- **Cold starts:** about 2 to 5 s for the first API call after idle (an estimate until measured). The page pings `/api/health` on load, so the backend is warm by the time a user has picked a document.
- **Concurrency:** one container serves many users; Cloud Run adds instances past a concurrency of about 4. OCR runs at most one or two jobs per container behind a semaphore, with the rest shown as queued.
- **Docker image:** one multi-stage image (Node builds the site, Python serves it) holds the API and the built site. FastAPI serves the site at `/` and the API at `/api` when the site is present, so `docker run -p 8000:8000 ghcr.io/jacksonfergusondev/star-ground` is a full self-hosted Star Ground. Cloud Run runs the same image, pushed to Artifact Registry. It listens on `${PORT:-8000}`.

### Product

- **Audience: the general DIY pedal builder.** Build nothing deep for one kind of user (for example, people who sell pedals) until forum feedback asks for it.
- **Local-first, no accounts in v1.** Builds, stock and checklist progress live in the browser, with share links and import and export. Accounts and sync are a README "if users ask" item.
- **Laptop-first bench mode.** Builds happen with a laptop at the bench, so bench mode is designed for a laptop screen and keyboard, then made to work on phones.
- **Cut from the Streamlit app:** the Debug Console, the "Advanced Options" expander and emoji headings. The feedback expander becomes in-app part reports.

### Publishers' documents

Star Ground stores only what it derives from a document, never the document itself:

- **Stored:** the publisher's URL, title, publisher, content hash, parsed parts list, and part positions.
- **Fetched on demand:** when a user opens a catalog build, the API fetches the PDF from the publisher (as "From URL" does today) and the browser draws it. Nothing is kept.
- **Build pack:** includes the source PDF only when the user uploaded it or it was fetched for them in that session; otherwise a link to the publisher's page.
- **Nightly check:** a workflow re-checks every catalog URL. A dead link hides the build; a changed hash triggers a re-parse and flags the build for review. Requests are conditional and spread out.
- **Good for publishers:** each catalog page links prominently to buy the PCB from its publisher, and the catalog and footer carry a contact note: "Are you a publisher? Let me know if you'd rather your documents weren't listed."
- **No copies in git.** The repository and its history hold no publisher PDFs (step 1).

This is a judgment call, not legal advice. Contacting publishers directly waits until there are consistent users.

## Steps

Each step lands as one or more pull requests. Steps 2 onward may overlap where they don't depend on each other.

### 1. Remove publisher PDFs from the repository and its history

The public repository holds publisher PDFs in `raw_boms/` (11, the presets' sources), `examples/Star_Ground_Artifacts/Source Documents/` (3), and `tests/samples/` (19, the CI accuracy corpus), plus earlier copies in history (45 PDF paths in all, including `examples/Pedal_Build_Pack_Complete/`). The Docker image also ships `raw_boms/`, since `.dockerignore` doesn't exclude it. This step goes first because rewriting history invalidates every branch and worktree, so it's cheapest while nothing else is in flight.

1. **Make the tree independent of the PDFs** (one PR):
    - **Test corpus:** replace `tests/samples/` with a manifest of each document's publisher URL and SHA-256. A `just fetch-samples` task downloads and verifies them into an ignored directory, and CI caches them by hash. Tests that need them skip with a clear message when they're absent. If a publisher changes a document, the hash check fails loudly, and the document is relabeled or replaced. The golden parts lists (`tests/golden/`) are derived data and stay.
    - **Presets:** `tools/generate_presets.py` reads publisher URLs instead of `raw_boms/`. Presets keep their parsed `bom_text`, and `source_path` becomes `source_url`. The build pack links to a preset's source instead of bundling it.
    - **Examples:** delete `examples/Star_Ground_Artifacts/Source Documents/`. The generated field manuals and sticker sheets are Star Ground's own output and stay.
    - **Docker:** exclude anything left that isn't needed at runtime.
1. **Rewrite history** with `git filter-repo` in a fresh mirror clone, removing every publisher PDF path ever committed (list them with `git log --all --name-only --diff-filter=A -- '*.pdf'`). Keep Star Ground's generated field manuals and sticker sheets.
1. **Force-push** `main` and all tags. The repository has no forks and one open PR (#163, Renovate's lock file maintenance), which is closed and left for Renovate to reopen. Tags keep their names, so releases point at the rewritten commits.
1. **Clean up what still holds old copies:**
    - Delete GHCR image versions built before the rewrite (they contain `raw_boms/`), and rebuild `latest` from the rewritten `main`.
    - Skipped: asking GitHub Support to purge the `refs/pull/*` refs of closed pull requests, which a force-push can't touch. The documents are ones their publishers give away, nothing in them is sensitive, and the old commits are reachable only by SHA through closed pull requests. Revisit only if a publisher asks.
    - Move local checkouts onto the rewritten history. Local branches and worktrees from before the rewrite must never be pushed.
1. **Verify** in a fresh clone that `git log --all -- '*.pdf'` lists only generated field manuals and sticker sheets, and that CI passes on the rewritten `main`.

### 2. API service

1. `src/server/`: a FastAPI app over `src/api.py`.
    - `POST /api/documents/read`: read an upload or link ahead of time (build choice, OCR offer).
    - `POST /api/master-list`: projects in, master list out (shopping list, warnings, option notes, stats).
    - Exports: shopping list CSV, supplier cart CSV, stock update CSV, PDF bundle, build pack.
    - Read progress as server-sent events: `queued` (waiting for a reader), `reading`, then `result` or `error`. Per-page OCR progress would need a hook in the OCR loop; add it in step 5 if the wait feels opaque.
    - `GET /api/health` for the warm-up ping, and `GET /api/presets` for the preset library.
    - Feedback reports appended to the Google Sheet, with the service account kept server-side.
1. Pydantic response models from the backend's dataclasses, and an OpenAPI schema checked into the repo for type generation.
1. Limits: the 50 MB upload cap, a cap on pages sent to OCR, an OCR semaphore, per-IP rate limiting, and CORS for the Pages origin only.
1. Tests with FastAPI's test client, under the existing rules (no network, `tmp_path` only).

**Done when** every Streamlit capability is reachable through the API.

### 3. Frontend foundation

1. `web/`: an Astro project with the Svelte integration and the website's lint, format and test setup. It installs house-style with npm, pinned to a tag in `web/package.json`, and loads `branding/theme.css` for the accent.
1. The landing page as a house page: section markers, the grid behind long pages, icons on standalone links, no emoji.
1. CI job for the frontend: lint, type check (`astro check`), build, Playwright with axe.

### 4. Walking skeleton, deployed

[`deploy/README.md`](deploy/README.md) explains the deploy and the one-time Google Cloud, Pages and DNS setup.

1. The multi-stage Docker image and the self-host path.
1. Cloud Run deploy from a tag (Artifact Registry, max instances, budget alert), and GitHub Pages deploy with the `CNAME`.
1. One end-to-end path live: paste a parts list, see the shopping list.
1. Confirm Cloud Run appends the client's address as the last `X-Forwarded-For` entry, which the rate limiter trusts with `STAR_GROUND_TRUST_PROXY=1`.
1. Measure real cold starts and OCR times on Cloud Run, and record them here.

**Done when** the deployed slice works and costs are confirmed.

### 5. Build workspace

The core of v1, replacing the form.

1. Projects as cards: add from presets, catalog, upload, link or pasted text; set a count; choose the build and opt into OCR on the card itself.
1. The master list updates as projects change, with no Generate button.
1. Stock upload, the shopping list with supplier links, and every export.
1. State in browser storage, surviving reloads.

**Done when** everything the Streamlit app does is possible here.

### 6. Document review

1. Backend: each part and warning carries its source location (page and box) from the layout reader and OCR.
1. A pdf.js viewer beside the parts list: each part outlined where it was read; a warning jumps to its spot.
1. Confirm or correct a part in place.
1. "This part looks wrong": one click sends the document link, location and correction to the feedback sheet, feeding the parser's regression corpus.

### 7. PCB and schematic highlighting

Probed on 2026-10-24: board renders in PedalPCB, GuitarPCB and Tonepad documents are embedded images with bounding boxes, and Aion's schematics are vector with real text. OCR of cropped board images found 14 designators in 0.5 to 2.2 s; it misses labels rotated 90°.

1. Backend: find the layout and schematic regions (largest embedded image on a layout page; vector text where present), and each designator's position (text positions, or OCR at 0° and 90°).
1. Hovering a part lights it up on the board or schematic, and hovering the board highlights the row. Unplaced parts simply don't light up.
1. Catalog documents get positions precomputed offline; uploads compute them on request.
1. Only if heuristics fail for a publisher: a small pre-trained layout model on onnxruntime (for example, RapidLayout). No training.

### 8. Circuit catalog

1. An offline job builds catalog data from publisher URLs: title, publisher, buy link, hash, parsed parts, positions. Only documents the parser reads cleanly are listed.
1. A static Astro page per build, with search and filters, "Add to build", the publisher's buy link and the publisher contact note.
1. The nightly check workflow (see [Publishers' documents](#publishers-documents)).

### 9. Bench mode

1. A laptop-first checklist per project, ordered by component height, with the board view and highlighting beside it.
1. Keyboard-driven: Space checks off the next part, arrows move. Progress is saved.
1. Field manual and sticker sheet PDFs stay as exports.

### 10. Share links

1. A build list encoded in a URL. Opening it loads the same projects into the workspace.

### 11. Cutover and launch

1. Delete `app.py`, `src/app_state.py`, `.streamlit/` and the Streamlit dependency outright.
1. Update the README (live app link, quick start, project structure) and the house-style install box.
1. Point the old `star-ground.streamlit.app` at the new site.
1. Run the launch check from a user's side on documents from each publisher, then post on builder forums.

## After launch, in order of feedback

- **Stock ledger:** stock changes only through events (order received, build completed, manual adjustment), so it never needs a full recount.
- **Stock-take entry:** a keyboard-first screen where `10k 23` adds 23 10k resistors, tied to bin labels.
- **"Verify, don't count":** expected stock from past orders minus completed builds' parts, checked bin by bin.
- **Tayda order email import:** paste an order confirmation or upload the `.eml` and get Star Ground's inventory CSV. Needs real order emails to build against.
- **Cost stats:** the all-in cost of each build, from imported orders.
- **Smarter buying:** cost per build and carts split by supplier, where supplier APIs allow.
- **Accounts and sync**, if users ask.

## Status

| Step | Status | Pull requests |
| --- | --- | --- |
| Plan | Done | This plan's PR |
| 1. Remove publisher PDFs | Done 2026-10-24: history rewritten, `main` and 15 tags force-pushed, old images deleted | #201 |
| 2. API service | Done 2026-10-24 | #203 |
| 3. Frontend foundation | Done 2026-10-25: the site uses house-style v2's shared shell | #204, #205 |
| 4. Walking skeleton | In progress: the image, deploy workflows and paste path are built; deploying waits on the Google Cloud setup | This PR |
| 5. Build workspace | Not started | |
| 6. Document review | Not started | |
| 7. PCB highlighting | Not started | |
| 8. Circuit catalog | Not started | |
| 9. Bench mode | Not started | |
| 10. Share links | Not started | |
| 11. Cutover and launch | Not started | |
