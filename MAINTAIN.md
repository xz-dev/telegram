# Downstream fork maintenance

This fork (`xz-dev/telegram`) tracks `mautrix/telegram` and carries a small
set of downstream patches that are being dogfooded before any upstream PR.
Maintenance follows the `downstream-fork-maintain` skill model:

- **`main`** is rebuilt from `upstream/main` + `ci` + patch branches by
  `.github/workflows/upstream-sync.yml` (force-push with lease). Do not
  commit product changes directly on `main`; they will be lost.
- **`ci`** (this branch) holds the sync workflow, this file, and the
  downstream README section. No product/runtime changes here.
- **`patch/<name>`** branches each hold one independently reviewable patch,
  based on `origin/main` of upstream. These are the source of truth for
  patch contents; fix conflicts and improvements there, then let the sync
  rebuild `main`.
- **`tmp/patch/<name>`** for cross-patch compatibility fixes that belong to
  no single patch; ordered right after the patches they depend on in the
  workflow.

## Current patch order (upstream-sync.yml)

1. `origin/ci` (squash; README.md conflicts resolved by taking ci's version)
2. `patch/relay-sticker-keep-sticker` — keep relayed `m.sticker` as real
   Telegram stickers; send attribution as a separate text message
   (mautrix/telegram#1092)
3. `patch/gif-alpha-threshold` — `paletteuse=alpha_threshold=128` for
   webm→gif sticker conversion so semi-transparent pixels binarize instead
   of blending

## Validation in the workflow

`go build ./...` and `go test ./...` run on the integrated tree before the
push. The `cmd/mautrix-telegram` link step requires libolm headers, which
are present on `ubuntu-latest` runners; if the job fails there it is a real
failure, not the known-local-missing-libolm issue.

## Required secrets

- `SYNC_WORKFLOW_TOKEN` — a PAT (or app token) with `contents:write` +
  `workflows` on this repo. Needed because the default `GITHUB_TOKEN`
  cannot push a rebuild that then triggers other workflows.

## Adding/retiring patches

- Add: create `patch/<name>` off upstream main, validate standalone, append
  a `git merge --squash origin/patch/<name>` + commit step in
  `upstream-sync.yml` (in dependency order), list it above.
- Retire: remove the squash step and list entry first, let the next sync
  rebuild `main`, then delete the patch branch.
