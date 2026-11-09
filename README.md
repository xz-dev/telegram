# mautrix-telegram

> **Downstream fork notice** — this is `xz-dev/telegram`, a personal
> downstream of `mautrix/telegram` that carries a few extra patches being
> dogfooded before upstreaming. See [`MAINTAIN.md`](MAINTAIN.md) for the
> branch model and patch list.

![Languages](https://img.shields.io/github/languages/top/mautrix/telegram.svg)
[![License](https://img.shields.io/github/license/mautrix/telegram.svg)](LICENSE)
[![Release](https://img.shields.io/github/release/mautrix/telegram/all.svg)](https://github.com/mautrix/telegram/releases)
[![GitLab CI](https://mau.dev/mautrix/telegram/badges/main/pipeline.svg)](https://mau.dev/mautrix/telegram/container_registry)

A Matrix-Telegram puppeting/relaybot bridge.

## Self-use builds

This fork publishes **prereleases**, not official mautrix releases. Download the
Linux amd64/arm64 archive, `SHA256SUMS` and `build-info.json` from
[GitHub Releases](https://github.com/xz-dev/telegram/releases). Verify the
checksum before extracting the matching archive:

```sh
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf mautrix-telegram-linux-amd64.tar.gz
./mautrix-telegram --version
```

The archives contain a statically linked bridge, license and provenance. They
**do not bundle ffmpeg or lottieconverter**: install those separately to convert
WebM/Lottie stickers. Animated WebP retains partial alpha; GIF only has binary
transparency. Follow the upstream setup instructions below for configuration,
appservice registration and database setup; back up config/database before
trying a new prerelease. Upstream's `--version` display uses `+dev.<commit>` for
fork builds; `build-info.json` records the full downstream release tag and SHA.

Alternatively use the matching multiarchitecture image (includes both
conversion tools), replacing the placeholders with values from the Release:

```sh
docker run --rm -v "$PWD/data:/data" ghcr.io/xz-dev/telegram:<version>
# Prefer the immutable reference from build-info.json for a fixed test version:
docker run --rm -v "$PWD/data:/data" ghcr.io/xz-dev/telegram@sha256:<digest>
```

The first container run writes `/data/config.yaml`; edit it, restart to generate
registration, and register the appservice with your homeserver. A newly created
GHCR package can be private; authenticate to pull it or explicitly change its
visibility. No `latest` tag is promoted by the release workflow.

Maintainers: daily sync does not publish. From generated main, run **Publish
self-use release** in Actions, or:

```sh
gh workflow run publish-release.yml --repo xz-dev/telegram --ref main
```

See [MAINTAIN.md](MAINTAIN.md) for rebuild, validation and authentication rules.

## Sponsors
* [Joel Lehtonen / Zouppen](https://github.com/zouppen)

## Documentation
All setup and usage instructions are located on
[docs.mau.fi](https://docs.mau.fi/bridges/go/telegram/index.html).
Some quick links:

* [Bridge setup](https://docs.mau.fi/bridges/go/setup.html?bridge=telegram)
  (or [with Docker](https://docs.mau.fi/bridges/general/docker-setup.html?bridge=telegram))
* Basic usage: [Authentication](https://docs.mau.fi/bridges/go/telegram/authentication.html)

### Features & Roadmap
[ROADMAP.md](ROADMAP.md) contains a general overview of what is supported by the bridge.

## Discussion
Matrix room: [`#telegram:maunium.net`](https://matrix.to/#/#telegram:maunium.net)

Telegram chat: [`mautrix_telegram`](https://t.me/mautrix_telegram) (bridged to Matrix room)
