#!/usr/bin/env bash
# Import only an explicitly provisioned CI signer; never export local keys.
set -euo pipefail
: "${REBUILD_GPG_PRIVATE_KEY:?Configure the dedicated CI signing key secret}"
: "${REBUILD_GPG_FINGERPRINT:?Configure the expected CI signing fingerprint}"
: "${REBUILD_GIT_NAME:?Configure the CI commit author name}"
: "${REBUILD_GIT_EMAIL:?Configure the CI commit author email}"
: "${RUNNER_TEMP:?}"
: "${GITHUB_ENV:?}"
export GNUPGHOME="$RUNNER_TEMP/rebuild-gnupg"
mkdir -m 700 "$GNUPGHOME"
printf '%s' "$REBUILD_GPG_PRIVATE_KEY" | gpg --batch --import
fingerprint=$(gpg --batch --with-colons --list-secret-keys "$REBUILD_GPG_FINGERPRINT" | awk -F: '$1 == "fpr" {print $10; exit}')
test "$fingerprint" = "$REBUILD_GPG_FINGERPRINT"
wrapper="$RUNNER_TEMP/rebuild-gpg"
cat > "$wrapper" <<'GPG'
#!/usr/bin/env bash
set -euo pipefail
exec gpg --batch --pinentry-mode loopback --passphrase-fd 3 "$@" 3<<<"${REBUILD_GPG_PASSPHRASE:-}"
GPG
chmod 700 "$wrapper"
git config user.name "$REBUILD_GIT_NAME"
git config user.email "$REBUILD_GIT_EMAIL"
git config user.signingkey "$REBUILD_GPG_FINGERPRINT"
git config gpg.program "$wrapper"
git config commit.gpgsign true
printf 'GNUPGHOME=%s\n' "$GNUPGHOME" >> "$GITHUB_ENV"
