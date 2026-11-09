# Self-use release slice

Owner-approved scope: Linux amd64/arm64 binary archives and a same-version
GHCR image; daily upstream sync, manual prerelease only; repair relay product
behavior on its patch branch and exclude the ineffective GIF-alpha patch.
No upstream PR.

## Observable acceptance examples

- Given exact upstream/ci/patch commits, replay into a new outside path;
  source files/refs/index stay unchanged and provenance names those inputs.
- Given the same vector again, produce the same integration tip; a complete
  existing marker reports unchanged. A changed input requires another replay.
- Given an existing/nested target, unexpected conflict, empty patch, unknown
  patch or dirty tracked source, stop without publishing/updating main.
- Given a manually selected generated main SHA, both native builders produce
  statically linked runnable archives, then images containing the same binary
  and working ffmpeg/Lottie conversion tools.
- Given matching successful native outputs, a manual release publishes a new
  prerelease with checksums, exact source/inputs and image digest. Mismatched
  outputs or any existing release/tag/image version stop; no overwrite retry.
- Given daily sync, successful native gates permit only an explicit leased main
  update, not a release. Missing sync auth is an explicit failure, not fallback.

## Implementation / evidence

- Replay CLI: `scripts/rebuild.py`; black-box fixture checks:
  `scripts/test_rebuild.py` (including deliberately broken-guard validation).
- Artifact preparation/validation/publication: `scripts/release.py`,
  `scripts/test_release.py`.
- Native build: upstream static builders via `scripts/release-build.Dockerfile`;
  image retains upstream `Dockerfile.ci` and `docker-run.sh` semantics;
  `scripts/smoke-image.sh` checks exact binary and exercises both converters.
- Automation: `.github/workflows/upstream-sync.yml`, `build-release.yml`,
  `publish-release.yml`. The CI branch owns these files; main is generated.

Passing local tests are implementation evidence, not user acceptance or proof
that hosted ARM64/GHCR publication ran. Final acceptance requires the parent to
observe the complete Actions run and download/start the actual release assets.
