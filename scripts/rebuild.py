#!/usr/bin/env python3
"""Replay exact downstream inputs in a new local clone; never fetch or push."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PATCH_ORDER = ("relay-sticker-keep-sticker",)
MARKER = ".downstream-inputs.json"


def git(root, *args, check=True, env=None):
    return subprocess.run(["git", "-C", str(root), *args], check=check,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def canonical(inputs):
    return json.dumps(inputs, sort_keys=True, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-patches", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--upstream")
    parser.add_argument("--ci")
    parser.add_argument("--patch", action="append", default=[])
    parser.add_argument("--check-current", help="Return 0 for identical inputs, 1 if rebuilding is needed")
    args = parser.parse_args()
    if args.list_patches:
        print("\n".join(PATCH_ORDER))
        return 0
    if not args.source or not args.upstream or not args.ci:
        parser.error("--source, --upstream and --ci are required")
    source = args.source.resolve(strict=True)
    if Path(git(source, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != source:
        parser.error("source must be a repository root")
    patches = {}
    for value in args.patch:
        name, sep, sha = value.partition("=")
        if not sep or name not in PATCH_ORDER or name in patches:
            parser.error("unknown, duplicate or malformed patch input: " + value)
        patches[name] = sha
    if set(patches) != set(PATCH_ORDER):
        parser.error("supply the complete enabled patch vector")
    inputs = {"schema": 1, "upstream": args.upstream, "ci": args.ci,
              "patches": [{"name": name, "sha": patches[name]} for name in PATCH_ORDER]}
    for sha in [args.upstream, args.ci, *patches.values(), *([args.check_current] if args.check_current else [])]:
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or git(source, "cat-file", "-t", sha).stdout.strip() != "commit":
            parser.error("every input must be a full local commit SHA")
    # Execute the reviewed ci driver, not an obsolete generated-main copy.
    expected_driver = subprocess.check_output(["git", "-C", str(source), "show", args.ci + ":scripts/rebuild.py"])
    if Path(__file__).read_bytes() != expected_driver:
        parser.error("running driver does not match the selected ci commit")
    marker = canonical(inputs)
    subject = "record downstream sync inputs"
    if args.check_current:
        recorded = git(source, "show", args.check_current + ":" + MARKER, check=False)
        message = git(source, "show", "-s", "--format=%s", args.check_current).stdout.strip()
        return 0 if recorded.returncode == 0 and recorded.stdout == marker and message == subject else 1
    if not args.target:
        parser.error("--target is required for replay")
    target = args.target.resolve()
    boundaries = [source,
                  Path(git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()),
                  Path(git(source, "rev-parse", "--absolute-git-dir").stdout.strip())]
    for line in git(source, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            boundaries.append(Path(line[9:]).resolve())
    if args.target.exists() or args.target.is_symlink() or any(target == p or target in p.parents or p in target.parents for p in boundaries):
        parser.error("target must be a new path outside the source, Git storage and all linked worktrees")
    if git(source, "diff", "--quiet", "HEAD", check=False).returncode:
        parser.error("source has tracked modifications; commit or preserve them before replay")
    subprocess.run(["git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(source), str(target)], check=True, stdout=sys.stderr)
    git(target, "checkout", "--detach", args.upstream)
    # Preserve the source repository's identity and signing policy in the clone.
    for key in ("user.name", "user.email", "user.signingkey", "gpg.format", "gpg.program", "commit.gpgsign"):
        setting = git(source, "config", "--get", key, check=False)
        if setting.returncode == 0:
            git(target, "config", key, setting.stdout.strip())
    stamp = git(target, "show", "-s", "--format=%ct", args.upstream).stdout.strip() + " +0000"
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)

    def commit(message):
        if git(target, "diff", "--cached", "--quiet", check=False).returncode == 0:
            raise ValueError("empty integration: " + message)
        git(target, "commit", "-m", message, env=env)

    # The overlay may replace the downstream README, but no other conflict.
    merged = git(target, "merge", "--squash", args.ci, check=False)
    conflicts = git(target, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    if merged.returncode and conflicts != ["README.md"]:
        raise ValueError("unexpected ci merge failure: " + merged.stderr + merged.stdout)
    if conflicts:
        git(target, "restore", "--source=" + args.ci, "--staged", "--worktree", "--", "README.md")
    changed = git(target, "diff", "--cached", "--name-only").stdout.splitlines()
    if any(p not in {"README.md", "MAINTAIN.md"} and not p.startswith(("scripts/", ".github/", "PLAN/")) for p in changed):
        raise ValueError("ci overlay contains a product change outside its ownership")
    commit("merge ci branch")
    for name in PATCH_ORDER:
        merged = git(target, "merge", "--squash", patches[name], check=False)
        if merged.returncode:
            raise ValueError("patch conflict: " + name + "\n" + merged.stderr + merged.stdout)
        commit("merge patch/" + name + " branch")
    (target / MARKER).write_text(marker)
    git(target, "add", "--force", "--", MARKER)
    commit(subject)
    print(git(target, "rev-parse", "HEAD").stdout.strip())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        print("rebuild: " + str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr or "", file=sys.stderr)
        sys.exit(2)
