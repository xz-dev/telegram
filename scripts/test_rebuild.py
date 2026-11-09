#!/usr/bin/env python3
"""Acceptance checks at the replay command boundary; only disposable repositories."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

DRIVER = Path(__file__).with_name("rebuild.py").resolve()


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-replay-", dir="/var/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        # Fixture history is isolated from the human's global Git identity/signing.
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git("init", "-b", "main")
        (self.source / "runtime.txt").write_text("upstream\n")
        self.upstream = self.commit("upstream")
        self.git("checkout", "-b", "ci")
        (self.source / "scripts").mkdir()
        (self.source / "scripts/rebuild.py").write_bytes(DRIVER.read_bytes())
        (self.source / "README.md").write_text("downstream\n")
        self.ci = self.commit("ci")
        self.git("checkout", "-b", "patch/relay-sticker-keep-sticker", self.upstream)
        (self.source / "runtime.txt").write_text("relay fixed\n")
        self.patch = self.commit("relay")
        self.git("checkout", "main")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], env=self.env, stderr=subprocess.PIPE, text=True).strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_driver(self, *extra, target=True):
        args = ["python3", str(DRIVER), "--source", str(self.source), "--upstream", self.upstream,
                "--ci", self.ci, "--patch", "relay-sticker-keep-sticker=" + self.patch]
        if target:
            args += ["--target", str(self.root / "candidate")]
        return subprocess.run([*args, *extra], env=self.env, capture_output=True, text=True)

    def test_rebuild_has_exact_provenance_and_preserves_source(self):
        before = (self.git("show-ref"), self.git("status", "--porcelain"), (self.source / "runtime.txt").read_bytes())
        result = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = self.root / "candidate"
        self.assertEqual((candidate / "runtime.txt").read_text(), "relay fixed\n")
        self.assertEqual(json.loads((candidate / ".downstream-inputs.json").read_text()),
                         {"schema": 1, "upstream": self.upstream, "ci": self.ci,
                          "patches": [{"name": "relay-sticker-keep-sticker", "sha": self.patch}]})
        self.assertEqual(before, (self.git("show-ref"), self.git("status", "--porcelain"), (self.source / "runtime.txt").read_bytes()))
        # Repeated exact vectors reproduce commit identity, not just file content.
        second = self.run_driver("--target", str(self.root / "second"))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(result.stdout, second.stdout)
        self.git("fetch", str(candidate), "HEAD")
        tip = self.git("rev-parse", "FETCH_HEAD")
        self.assertEqual(self.run_driver("--check-current", tip, target=False).returncode, 0)
        self.assertEqual(self.run_driver("--check-current", self.upstream, target=False).returncode, 1)
        self.git("checkout", "patch/relay-sticker-keep-sticker")
        (self.source / "runtime.txt").write_text("another fix\n")
        self.patch = self.commit("changed input")
        self.assertEqual(self.run_driver("--check-current", tip, target=False).returncode, 1)

    def test_generated_provenance_is_recorded_even_if_globally_ignored(self):
        excludes = self.root / "global-ignore"
        excludes.write_text(".downstream*\n")
        config = self.root / "global-config"
        config.write_text("[core]\n\texcludesFile = " + str(excludes) + "\n")
        self.env["GIT_CONFIG_GLOBAL"] = str(config)
        result = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = subprocess.check_output(
            ["git", "-C", str(self.root / "candidate"), "show", "HEAD:.downstream-inputs.json"],
            env=self.env, text=True)
        self.assertEqual(json.loads(contents)["upstream"], self.upstream)

    def test_signed_source_policy_is_preserved_in_generated_commits(self):
        home = self.root / "gnupg"
        home.mkdir(mode=0o700)
        self.env["GNUPGHOME"] = str(home)
        def gpg(*args):
            return subprocess.check_output(["gpg", "--batch", *args], env=self.env,
                                           text=True, stderr=subprocess.PIPE)
        gpg("--pinentry-mode", "loopback", "--passphrase", "", "--quick-generate-key",
            "Fixture <fixture@example.invalid>", "ed25519", "sign", "0")
        self.addCleanup(subprocess.run, ["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fingerprint = next(line.split(":")[9] for line in gpg("--with-colons", "--list-secret-keys").splitlines()
                           if line.startswith("fpr:"))
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.signingkey", fingerprint)
        self.git("config", "commit.gpgsign", "true")
        result = self.run_driver()
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = self.root / "candidate"
        def candidate_git(*args):
            return subprocess.check_output(["git", "-C", str(candidate), *args],
                                           env=self.env, text=True, stderr=subprocess.PIPE).strip()
        self.assertEqual(candidate_git("config", "user.signingkey"), fingerprint)
        self.assertEqual(candidate_git("config", "commit.gpgsign"), "true")
        commits = candidate_git("rev-list", self.upstream + "..HEAD").splitlines()
        self.assertEqual(len(commits), 3)
        for commit in commits:
            candidate_git("verify-commit", commit)
            self.assertEqual(candidate_git("show", "-s", "--format=%GF", commit), fingerprint)

    def test_refuses_existing_nested_and_git_targets_without_touching_them(self):
        for target in [self.source, self.source / "nested", self.source / ".git/nested", self.root]:
            result = self.run_driver("--target", str(target))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("target must be a new path", result.stderr)
        self.assertEqual((self.source / "runtime.txt").read_text(), "upstream\n")

    def test_refuses_linked_worktree_and_dangling_symlink_targets(self):
        linked = self.root / "linked"
        self.git("worktree", "add", "--detach", str(linked), self.upstream)
        result = self.run_driver("--target", str(linked / "nested"))
        self.assertEqual(result.returncode, 2)
        self.assertFalse((linked / "nested").exists())
        alias = self.root / "dangling"
        alias.symlink_to(self.root / "missing")
        result = self.run_driver("--target", str(alias))
        self.assertEqual(result.returncode, 2)
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.root / "missing").exists())

    def test_conflicting_patch_stops_instead_of_resolving_runtime(self):
        self.git("checkout", "ci")
        # This is a new upstream, conflicting with the original patch, not a ci product override.
        self.git("checkout", "-b", "new-upstream", self.upstream)
        (self.source / "runtime.txt").write_text("upstream changed same line\n")
        self.upstream = self.commit("new upstream")
        self.git("checkout", "main")
        result = self.run_driver()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("patch conflict", result.stderr)

    def test_empty_and_incomplete_inputs_fail_closed(self):
        self.patch = self.upstream
        result = self.run_driver()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("empty integration", result.stderr)
        result = self.run_driver("--patch", "unknown=" + self.upstream)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown", result.stderr)

    def test_dirty_source_is_not_replayed(self):
        (self.source / "runtime.txt").write_text("uncommitted work\n")
        result = self.run_driver()
        self.assertEqual(result.returncode, 2)
        self.assertIn("tracked modifications", result.stderr)
        self.assertEqual((self.source / "runtime.txt").read_text(), "uncommitted work\n")

    def test_unexpected_ci_product_edit_is_rejected(self):
        self.git("checkout", "ci")
        (self.source / "runtime.txt").write_text("not overlay owned\n")
        self.ci = self.commit("misplaced product change")
        self.git("checkout", "main")
        result = self.run_driver()
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside its ownership", result.stderr)


if __name__ == "__main__":
    unittest.main()
