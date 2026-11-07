# Downstream maintenance

This fork is a self-use test distribution of `mautrix/telegram`. Do not submit
these patches upstream until the owner has completed dogfooding.

## Ownership and active inputs

- Upstream `mautrix/telegram:main` is always the source baseline.
- `ci` owns workflows, `scripts/`, `PLAN/`, this guide and the downstream README.
  It must not contain product/runtime edits.
- `patch/relay-sticker-keep-sticker` owns connector-managed relay attribution
  and preserving real Telegram stickers ([upstream issue #1092](https://github.com/mautrix/telegram/issues/1092)).
- Generated `main` contains one squash integration for `ci`, then one for each
  enabled patch, followed by an exact-input marker commit. Never fix a product
  problem only on generated `main`.
- `patch/gif-alpha-threshold` is **retired from integration**, not deleted:
  `paletteuse` already defaults to `alpha_threshold=128`, so that patch did not
  fix transparency. A real GIF edge-quality change needs visual regression
  examples and its own reviewed patch. GIF cannot preserve partial alpha.

The only source of integration order is `PATCH_ORDER` in `scripts/rebuild.py`.
The current active product input is `relay-sticker-keep-sticker`. Input SHAs are
resolved automatically per run, not maintained in a hand-edited pin table.

## Rebuild and validation

`Upstream Sync` runs daily at **04:42 UTC** and can also be dispatched manually.
It fetches the current upstream, ci and enabled patch tips once. The driver
runs in a new standalone scratch clone outside the source and all linked
worktrees. It does not fetch, push, remove existing paths or alter the source
index, refs or files. Tracked source edits are refused; unrelated untracked
files are not inputs. The selected ci commit must contain the exact driver
bytes being executed.

The driver records `.downstream-inputs.json` with the complete upstream/ci/
ordered-patch SHA vector. Its marker makes identical inputs a no-op. Each
nonempty overlay has one countable integration commit; unexpected conflicts
or an empty patch stop the replay. Only an expected README conflict may take
the ci README. Runtime conflicts must be repaired on the owning patch branch.

Local replay (the target must **not exist**):

```sh
python3 /path/to/selected-ci/scripts/rebuild.py \
  --source /path/to/repository --target /var/tmp/new-telegram-candidate \
  --upstream <full-sha> --ci <full-sha> \
  --patch relay-sticker-keep-sticker=<full-sha>
```

Run the command-boundary checks with:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

Before updating main, CI builds and tests **both architectures natively**, uses
upstream's static builders, verifies the binaries have no ELF interpreter or
NEEDED libraries, starts them, builds the matching runtime images and exercises
ffmpeg/WebM and Lottie/GIF conversion. Only then may it push with
`--force-with-lease=refs/heads/main:<captured-old-sha>`. A lease failure means
another writer changed main: investigate and restart, never force unconditionally.

The normal host `go build ./...` path needs libolm development headers; GitHub
Ubuntu runners do **not** provide those implicitly. Upstream's static builders
already provide the static library and C toolchain. Their exact image digest
and actual Go version are recorded per build.

## Manual self-use releases

For the first bootstrap or an unmerged maintenance change, push a signed,
complete replay to `candidate/<name>`. `Validate release candidate` builds and
smoke-tests both native architectures from that exact candidate without
updating main, creating a Release or pushing any image. Adopt only the tested
candidate SHA after review. Once installed on main, the validation workflow
can also be dispatched with an optional exact candidate SHA.

`Publish self-use release` is **manual only**, and accepts only generated main.
A dispatch freezes that main SHA into a Git bundle; moving main later does not
change the source being built. Empty version input derives a unique downstream
prerelease tag from the upstream version, source date and source SHA. A custom
tag must look like `v0.2610.0-xz.<unique-suffix>`.

Both native runners build archives and smoke-tested image archives before any
publication. The publish job verifies their source/version/platform and binary
hashes, creates a draft, uploads GHCR architecture images and a multiarch index,
then uploads the two downloads, `SHA256SUMS` and `build-info.json`. The latter
records source/inputs, builder digests, toolchain, binary hashes and the
immutable container digest reference. Only after these steps does it publish
the draft as a prerelease (never `latest`). Existing release, Git tag or image
versions are refused. The workflow does not enforce registry-side tag
immutability against unrelated authorized writers; use the recorded digest.

A failed publication may leave a draft and orphaned versioned image tags. Inspect
that state and choose a **new version** for the next attempt; do not delete or
overwrite assets to make a rerun appear successful. A first GHCR package may be
private: authenticate to pull it, or explicitly make the package public in the
GitHub package settings. Publication does not change package visibility.

Actions downloads expire after seven days; GitHub Release downloads do not.
No upstream PR is created by any workflow.

The sticker is the canonical Matrix mapping. A separate attribution message
is tracked for exact-ID echo suppression and deletion. Replies in Telegram to
that auxiliary attribution have no Matrix reply target; reply to the sticker
itself. Sticker edits are rejected instead of silently changing media type.
This release does not improve GIF semi-transparency.

## Authentication and signing

- Daily sync requires `SYNC_WORKFLOW_TOKEN`, an authorized PAT/App token with
  repository contents and workflow write rights. This permits workflow updates
  and downstream push triggers. A missing secret is a hard, explicit stop;
  no existing local gh credential is copied into repository secrets.
- Manual release uses the job's `GITHUB_TOKEN` with contents/packages write.
  It does not require the sync PAT. GHCR login uses that same authorized token.
- Local maintenance and generated integration commits retain GPG signing.
  The replay driver copies the source repository's identity/signing settings;
  it never disables signing. Unlock the local signing key when prompted.
- Automated replay additionally needs a dedicated `REBUILD_GPG_PRIVATE_KEY`
  secret (armored private signing key), optional `REBUILD_GPG_PASSPHRASE`, and
  repository variables `REBUILD_GPG_FINGERPRINT`, `REBUILD_GIT_NAME`, and
  `REBUILD_GIT_EMAIL`. Provision these explicitly; tooling never exports or
  uploads the local key. Missing signer configuration stops the sync before
  any commit or main update. Manual release does not create commits and does
  not require these secrets when publishing an already signed local rebuild.
- Fixture tests use isolated throwaway Git histories and do not change the
  maintainer's Git configuration. Unsigned fixtures reproduce the same commit
  ID; signed replay preserves exact inputs and tree, but GPG signature times
  can give a fresh replay a different commit ID. The complete input-vector
  check prevents unnecessary main rewrites in either case.

To add a patch, create and validate its owning branch, add it to `PATCH_ORDER`,
update this guide, and rerun standalone and integrated gates. To retire one,
remove its active input/documentation first, rebuild and verify main, then only
archive/delete the branch with the owner's approval.
