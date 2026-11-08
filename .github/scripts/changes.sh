#!/usr/bin/env bash
set -euo pipefail

# Inputs to the static site and the combined Docker image.
site=(web/src/ web/public/ web/astro.config.mjs web/svelte.config.js web/tsconfig.json
  web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml web/.npmrc
  branding/theme.css catalog/builds/)
backend=(src/ pyproject.toml uv.lock)
image=("${backend[@]}" README.md LICENSE Dockerfile .dockerignore "${site[@]}")
ci=(.github/workflows/ci.yml .github/scripts/ .github/actions/)
production=("${image[@]}" .github/workflows/deploy.yml .github/workflows/deploy-production.yml)

emit() {
  printf '%s=true\n' "$@" >> "$GITHUB_OUTPUT"
}

verified_deployment() {
  local deployment id sha state
  deployment=$(gh api "repos/$GITHUB_REPOSITORY/deployments?environment=production-verification&ref=main&per_page=1" \
    --jq '.[0] // empty | [.id, .sha] | @tsv') || return 0
  read -r id sha <<< "$deployment"
  [[ "$id" =~ ^[0-9]+$ && "$sha" =~ ^[0-9a-f]{40}$ ]] || return 0
  state=$(gh api "repos/$GITHUB_REPOSITORY/deployments/$id/statuses?per_page=1" \
    --jq '.[0].state // ""') || return 0
  if [[ "$state" == success ]] && git merge-base --is-ancestor "$sha" "$GITHUB_SHA"; then
    printf '%s\n' "$sha"
  fi
}

production_pending() {
  local touched difference
  if [[ -z "$verified" ]]; then
    echo "No verified production baseline is available. Prepare a full deployment." >> "$GITHUB_STEP_SUMMARY"
    return
  fi
  # Even a reverted change may have reached production in an interrupted deploy.
  # Follow the first-parent history to include pushes that were superseded.
  touched=$(git log --first-parent -1 --format=%H "$verified..$GITHUB_SHA" -- "${production[@]}")
  difference=$(git diff --name-only "$verified" "$GITHUB_SHA" -- "${production[@]}")
  if [[ -z "$touched$difference" ]]; then
    deploy=false
    echo "Production inputs have stayed the same since the verified deployment." >> "$GITHUB_STEP_SUMMARY"
  fi
}

select_if_changed() {
  local check=$1 changes
  shift
  changes=$(git diff --name-only "$base" "$GITHUB_SHA" -- "$@")
  if [[ -n "$changes" ]]; then
    emit "$check"
  fi
}

select_checks() {
  local ci_changes
  ci_changes=$(git diff --name-only "$base" "$GITHUB_SHA" -- "${ci[@]}")
  if [[ -n "$ci_changes" ]]; then
    emit markdown workflows python web container
    return
  fi
  select_if_changed markdown ':(glob)**/*.md' pyproject.toml
  select_if_changed workflows .github/ pyproject.toml uv.lock
  select_if_changed python "${backend[@]}" tests/ ':(glob)scripts/**/*.py' deploy/cutoff/ \
    .python-version openapi.json
  select_if_changed web web/ branding/theme.css openapi.json catalog/builds/
  select_if_changed container "${image[@]}" .python-version \
    .github/workflows/container-test.yml scripts/_browser_smoke.py scripts/smoke_container.py \
    scripts/container_checks.py
}

if [[ "$GITHUB_REF" == refs/tags/* ]]; then
  emit markdown workflows python web container
  exit
fi

if [[ "$GITHUB_EVENT_NAME" == push && "$GITHUB_REF" == refs/heads/main ]]; then
  verified=$(verified_deployment)
  deploy=true
  production_pending
  if [[ "$deploy" == true ]]; then
    emit deploy container
  fi
  if [[ "${1:-}" == production ]]; then
    exit
  fi
  if [[ -z "$verified" ]]; then
    emit markdown workflows python web container
    exit
  fi
  # Main may be carrying changes from a push that never deployed.
  if [[ "$deploy" == true ]]; then
    base=$verified
    select_if_changed python "${backend[@]}"
    select_if_changed web "${site[@]}" openapi.json
  fi
fi

if [[ "$GITHUB_EVENT_NAME" == pull_request ]]; then
  base=$(git rev-parse "$GITHUB_SHA^1")
else
  base=${PUSH_BEFORE:-}
fi

if [[ ! "$base" =~ ^[0-9a-f]{40}$ ]] || ! git cat-file -e "$base^{commit}"; then
  emit markdown workflows python web container
else
  select_checks
fi
