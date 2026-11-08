# Repository Scripts

## OpenAPI Schema (`scripts/export_openapi.py`)

`openapi.json` at the repository root is the HTTP API's schema, which the web frontend generates its TypeScript types from. After changing a route or model in `src/star_ground/server/`, regenerate it with `just openapi` and commit it with the change. `tests/test_server.py` fails when the committed file no longer matches the API.

## Release Preflight (`scripts/prepare_release.py`)

`just bump <major|minor|patch>` runs `scripts/prepare_release.py` before invoking the shared release script:

1. Verifies the checkout is on a clean `main` branch synced with `origin/main`.
1. Requires a passing `ci.yml` run on `HEAD`, offering to watch an in-progress run to completion.
1. Checks `uv lock --check` before handing off to the version bump.

## Container Release Gate

The `Container` job in `ci.yml` calls `container-test.yml` on pull requests, on pushes to main (through `deploy.yml`) and on release tags. `CI OK` requires `linux/amd64` on `ubuntu-24.04` and `linux/arm64` on `ubuntu-24.04-arm` to pass. These run in parallel on native hardware, without QEMU. ARM64 supports Linux ARM hosts and Docker Linux VMs on Apple Silicon and Windows ARM. Deploys and release tags call the full `ci.yml` workflow, including these container tests, before shipping the image.

To run the gate locally with Docker running:

```bash
uv sync
uv run playwright install chromium
docker build -t star-ground:smoke .
uv run python scripts/smoke_container.py star-ground:smoke --artifacts /tmp/star-ground-smoke
```

The runner checks the image's default command, Docker health status, HTTP health response, web entry point and graceful SIGTERM shutdown. A separate container executes `container_checks.py` with networking disabled. It checks PDF parsing, inventory subtraction, CSV exports, generated PDF and ZIP contents, and real OCR inference. Both containers use a read-only root filesystem, temporary writable directories, dropped capabilities and the image's non-root user. No checkout, development dependencies or test suite is installed over the app. Chromium also drives the real server: it opens the landing page, adds a pasted parts list in the workspace, and requires each part in the master list the API returns. Browser traces and a screenshot are retained with diagnostics. Container logs and inspection data are printed on success and failure; CI retains diagnostics for seven days.

Each architecture is built once into an OCI archive with an SBOM and maximum provenance. On deploy and release runs, only passing archives are uploaded as one-day workflow artifacts. A deploy copies the `linux/amd64` archive to Artifact Registry for Cloud Run. The publisher copies those archives with digest preservation, checks that the combined index contains exactly their manifests, signs its immutable digest with Cosign, verifies the signature, then promotes it to the release tags. It never rebuilds the app. Registry and signing permissions belong only to the publisher. Run-specific candidate tags can remain in GHCR after a failed publication; they are not release tags.

A release tag must match `project.version`. Stable releases publish the full version, major/minor and `latest` tags. Prerelease tags publish only their full version. Publishing is serialized to avoid concurrent writes to floating tags. OCI provenance and SBOMs describe the build and its dependencies; they do not replace vulnerability scanning or comprehensive browser regression tests.

## Live Check

After a deploy, `deploy.yml` runs `smoke_live.py` against the live site. It waits up to ten minutes for `version.json` to name the deployed commit, asking past the CDN's cache, then drives Chromium through the same paste-a-parts-list path as the container gate, against the real API. To run it by hand:

```bash
uv run python -m scripts.smoke_live https://star-ground.jacksonferguson.me "$(git rev-parse origin/main)"
```
