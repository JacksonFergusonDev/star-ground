# Repository Tools

## Preset Generator (`tools/generate_presets.py`)

`tools/generate_presets.py` builds the app's presets library, `src/bom_lib/_presets_data.py`, from `raw_boms/`.

1. Add the preset's source:
    - **A publisher's build document:** add its URL and category to `raw_boms/<source>/sources.json`, e.g. `{"category": "fuzz", "url": "https://docs.pedalpcb.com/project/Sunflower-PedalPCB.pdf"}` in `raw_boms/pedalpcb/sources.json`. The script downloads it to parse it; the repository never keeps publishers' documents, and the build pack links to them.
    - **A typed parts list:** put the `.txt` file under `raw_boms/<source>/<category>/`, e.g. `raw_boms/tayda/fuzz/big_muff.txt`.

   The folder names (and `category`) become the source and category the app's preset browser filters by.
1. Run the script from the project root (it needs the internet):

   ```bash
   uv run python -m tools.generate_presets
   ```

1. Review the diff of `src/bom_lib/_presets_data.py` and commit it. Never edit that file by hand.

### Supported Formats

- **PDF build documents:** parsed with the app's own parser. A document that describes several circuits becomes one preset per build, named "Title: Build".
- **Text (`.txt`):** a `REF VALUE` list, used as written (the Tayda presets).

## OpenAPI Schema (`tools/export_openapi.py`)

`openapi.json` at the repository root is the HTTP API's schema, which the web frontend generates its TypeScript types from. After changing a route or model in `src/server/`, regenerate it with `just openapi` and commit it with the change. `tests/test_server.py` fails when the committed file no longer matches the API.

## Release Preflight (`tools/prepare_release.py`)

`just bump <major|minor|patch>` runs `tools/prepare_release.py` before invoking the shared release script:

1. Verifies the checkout is on a clean `main` branch synced with `origin/main`.
1. Regenerates `src/bom_lib/_presets_data.py` and offers to commit and push any changes.
1. Requires a passing `ci.yml` run on `HEAD`, offering to watch an in-progress run to completion.
1. Checks `uv lock --check` before handing off to the version bump.

## Container Release Gate

The `Container` job in `ci.yml` calls `container-test.yml` on pull requests and main. `CI OK` requires `linux/amd64` on `ubuntu-24.04` and `linux/arm64` on `ubuntu-24.04-arm` to pass. These run in parallel on native hardware, without QEMU. ARM64 supports Linux ARM hosts and Docker Linux VMs on Apple Silicon and Windows ARM. Release tags call the full `ci.yml` workflow, including these container tests, before publishing.

To run the gate locally with Docker running:

```bash
uv sync
uv run playwright install chromium
docker build -t star-ground:smoke .
uv run python tools/smoke_container.py star-ground:smoke --artifacts /tmp/star-ground-smoke
```

The runner checks the image's default command, Docker health status, HTTP health response, web entry point and graceful SIGTERM shutdown. A separate container executes `container_checks.py` with networking disabled. It checks every preset's typed parts list or publisher link, PDF parsing, inventory subtraction, CSV exports, generated PDF and ZIP contents, and real OCR inference. Both containers use a read-only root filesystem, temporary writable directories, dropped capabilities and the image's non-root user. No checkout, development dependencies or test suite is installed over the app. Chromium also drives the real server: it opens the landing page, pastes a parts list into the app page, and requires each part in the shopping list the API returns. Browser traces and a screenshot are retained with diagnostics. Container logs and inspection data are printed on success and failure; CI retains diagnostics for seven days.

Each architecture is built once into an OCI archive with an SBOM and maximum provenance. On release runs, only passing archives are uploaded as one-day workflow artifacts. The publisher copies those archives with digest preservation, checks that the combined index contains exactly their manifests, signs its immutable digest with Cosign, verifies the signature, then promotes it to the release tags. It never rebuilds the app. Registry and signing permissions belong only to the publisher. Run-specific candidate tags can remain in GHCR after a failed publication; they are not release tags.

A release tag must match `project.version`. Stable releases publish the full version, major/minor and `latest` tags. Prerelease tags publish only their full version. Publishing is serialized to avoid concurrent writes to floating tags. OCI provenance and SBOMs describe the build and its dependencies; they do not replace vulnerability scanning or comprehensive browser regression tests.
