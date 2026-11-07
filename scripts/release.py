#!/usr/bin/env python3
"""Prepare, package and publish one exact, immutable downstream prerelease."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import urllib.error
import urllib.request

REPO = "xz-dev/telegram"
IMAGE = "ghcr.io/" + REPO
ARCHES = ("amd64", "arm64")


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_version(value):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-xz\.[0-9A-Za-z.-]+", value):
        raise ValueError("version must be a downstream prerelease such as v0.2610.0-xz.20261109.abcdef123456")
    return value


def source_info(source):
    sha = run("git", "rev-parse", "HEAD", cwd=source)
    if run("git", "status", "--porcelain", "--untracked-files=no", cwd=source):
        raise ValueError("release source has tracked modifications")
    inputs = json.loads((source / ".downstream-inputs.json").read_text())
    if inputs.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{40}", inputs.get("upstream", "")) or not re.fullmatch(r"[0-9a-f]{40}", inputs.get("ci", "")):
        raise ValueError("invalid downstream provenance")
    if len(inputs.get("patches", [])) != 1 or inputs["patches"][0].get("name") != "relay-sticker-keep-sticker" or not re.fullmatch(r"[0-9a-f]{40}", inputs["patches"][0].get("sha", "")):
        raise ValueError("unexpected active patch vector")
    if run("git", "show", "-s", "--format=%s", "HEAD", cwd=source) != "record downstream sync inputs":
        raise ValueError("release only a complete generated-main marker commit")
    return sha, inputs


def prepare(args):
    source = args.source.resolve()
    sha, inputs = source_info(source)
    if args.version:
        version = valid_version(args.version)
    else:
        base = run("git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", inputs["upstream"], cwd=source)
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", base):
            raise ValueError("upstream base must have an official version tag")
        date = run("git", "show", "-s", "--format=%cs", sha, cwd=source).replace("-", "")
        version = valid_version(base + "-xz." + date + "." + sha[:12])
    args.output.mkdir(parents=True, exist_ok=False)
    run("git", "bundle", "create", str(args.output / "source.bundle"), "HEAD", cwd=source)
    write_json(args.output / "release-inputs.json", {"version": version, "source_commit": sha, "inputs": inputs})
    print(json.dumps({"version": version, "source_commit": sha}))


def package(args):
    version = valid_version(args.version)
    sha, inputs = source_info(args.source)
    native = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if native != args.arch:
        raise ValueError("package/smoke must run natively on the declared architecture")
    binary = args.built / "mautrix-telegram"
    headers = run("readelf", "-l", str(binary))
    dynamic = run("readelf", "-d", str(binary))
    if "INTERP" in headers or "NEEDED" in dynamic:
        raise ValueError("binary is not statically linked")
    version_output = run(str(binary), "--version")
    build_settings = run("go", "version", "-m", str(binary))
    # Upstream ProgramVersion intentionally displays non-official tags as
    # +dev.<commit>; verify its linker inputs as well as native startup.
    if sha[:8] not in version_output or "main.Tag=" + version not in build_settings or "main.Commit=" + sha not in build_settings:
        raise ValueError("binary does not embed the requested release version/commit")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", args.builder):
        raise ValueError("record an exact builder image digest, not a floating tag")
    info = {"version": version, "source_commit": sha, "inputs": inputs, "platform": "linux/" + args.arch,
            "builder_image": args.builder, "go_version": (args.built / "go-version.txt").read_text().strip(),
            "binary_sha256": sha256(binary), "version_output": version_output,
            "validation": ["native --version", "no ELF interpreter or dynamic NEEDED libraries", "go test ./... in static builder"]}
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / ("build-info-linux-" + args.arch + ".json"), info)
    files = {"mautrix-telegram": binary.read_bytes(), "build-info.json": (json.dumps(info, indent=2) + "\n").encode()}
    for name in ["LICENSE", "LICENSE.exceptions", "README.md"]:
        files[name] = (args.source / name).read_bytes()
    archive = args.output / ("mautrix-telegram-linux-" + args.arch + ".tar.gz")
    with tarfile.open(archive, "w:gz") as out:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name == "mautrix-telegram" else 0o644
            member.size = len(data)
            out.addfile(member, io.BytesIO(data))
    print(archive)


def collect(args):
    version = valid_version(args.version)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        raise ValueError("source SHA must be full length")
    infos = []
    for arch in ARCHES:
        info = json.loads((args.assets / ("build-info-linux-" + arch + ".json")).read_text())
        if info["version"] != version or info["source_commit"] != args.source_sha or info["platform"] != "linux/" + arch:
            raise ValueError("artifact metadata does not match the requested version/commit/platform")
        with tarfile.open(args.assets / ("mautrix-telegram-linux-" + arch + ".tar.gz")) as archive:
            if set(archive.getnames()) != {"mautrix-telegram", "build-info.json", "LICENSE", "LICENSE.exceptions", "README.md"}:
                raise ValueError("unexpected archive inventory")
            binary = archive.extractfile("mautrix-telegram").read()
            embedded = json.load(archive.extractfile("build-info.json"))
            if hashlib.sha256(binary).hexdigest() != info["binary_sha256"] or embedded != info:
                raise ValueError("archive binary or embedded provenance differs from the native build")
        infos.append(info)
    if infos[0]["inputs"] != infos[1]["inputs"]:
        raise ValueError("architecture builds have different downstream inputs")
    write_json(args.assets / "build-info.json", {"version": version, "source_commit": args.source_sha,
               "inputs": infos[0]["inputs"], "builds": infos})
    return infos


def publish(args):
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("publication is restricted to " + REPO)
    infos = collect(args)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("an authorized GitHub publication token is required")

    def api(path):
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + "/" + path,
                                         headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    # Authenticate before treating a 404 as absence; never reuse an existing version.
    if not api(""):
        raise ValueError("repository is not accessible to the publication token")
    if api("git/ref/tags/" + args.version) or api("releases/tags/" + args.version):
        raise ValueError("release/tag already exists; choose a new version, never overwrite")
    # Distinguish registry absence from authentication/transport failure.
    basic = base64.b64encode((os.environ["GITHUB_ACTOR"] + ":" + token).encode()).decode()
    request = urllib.request.Request("https://ghcr.io/token?service=ghcr.io&scope=repository:" + REPO + ":pull",
                                     headers={"Authorization": "Basic " + basic})
    with urllib.request.urlopen(request) as response:
        registry_token = json.load(response)["token"]
    for suffix in ["", "-amd64", "-arm64"]:
        request = urllib.request.Request("https://ghcr.io/v2/" + REPO + "/manifests/" + args.version + suffix,
            headers={"Authorization": "Bearer " + registry_token,
                     "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"})
        try:
            with urllib.request.urlopen(request):
                raise ValueError("container version already exists; never overwrite")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    # Create the draft before any remote image mutation. Failures leave a visible
    # draft and possibly orphaned immutable image tags, never a completed release.
    notes = args.assets / "release-notes.md"
    notes.write_text("Personal test prerelease. Source: `" + args.source_sha + "`.\n\nDo not deploy without backing up your config and database.\n")
    run("gh", "release", "create", args.version, "--repo", REPO, "--target", args.source_sha,
        "--draft", "--prerelease", "--title", args.version, "--notes-file", str(notes))
    digests = {}
    for arch in ARCHES:
        run("docker", "load", "--input", str(args.assets / ("image-linux-" + arch + ".tar")))
        image_info = json.loads(run("docker", "image", "inspect", "telegram-release:" + arch))[0]
        labels = image_info["Config"].get("Labels", {})
        if image_info["Architecture"] != arch or labels.get("org.opencontainers.image.revision") != args.source_sha or labels.get("org.opencontainers.image.version") != args.version:
            raise ValueError("container artifact does not match the source/version/platform")
        tag = IMAGE + ":" + args.version + "-" + arch
        # The workflow concurrency lock and unique Git tag/draft prevent this
        # version from being published twice. No --amend or overwrite retry.
        run("docker", "tag", "telegram-release:" + arch, tag)
        run("docker", "push", tag)
        digest = run("docker", "buildx", "imagetools", "inspect", tag, "--format", "{{.Manifest.Digest}}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("invalid architecture image digest")
        digests[arch] = digest
    run("docker", "buildx", "imagetools", "create", "--tag", IMAGE + ":" + args.version,
        *[IMAGE + "@" + digests[arch] for arch in ARCHES])
    digest = run("docker", "buildx", "imagetools", "inspect", IMAGE + ":" + args.version, "--format", "{{.Manifest.Digest}}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid multiarchitecture image digest")
    info_path = args.assets / "build-info.json"
    info = json.loads(info_path.read_text())
    info["container"] = {"reference": IMAGE + "@" + digest, "version_tag": IMAGE + ":" + args.version, "architectures": digests}
    write_json(info_path, info)
    assets = [args.assets / ("mautrix-telegram-linux-" + arch + ".tar.gz") for arch in ARCHES] + [info_path]
    checksum = args.assets / "SHA256SUMS"
    checksum.write_text("".join(sha256(p) + "  " + p.name + "\n" for p in assets))
    notes.write_text(notes.read_text() + "\n## Container\n\n`" + IMAGE + ":" + args.version + "`\n\nDigest-pinned: `" + IMAGE + "@" + digest + "`\n\n## Inputs\n\n```json\n" + json.dumps(info["inputs"], indent=2) + "\n```\n\nBinary archives include the bridge and license; install ffmpeg/lottieconverter separately for sticker conversion. The image includes both converters.\n")
    run("gh", "release", "upload", args.version, "--repo", REPO, *[str(p) for p in [*assets, checksum]])
    run("gh", "release", "edit", args.version, "--repo", REPO, "--notes-file", str(notes), "--draft=false", "--prerelease", "--latest=false")
    print("https://github.com/" + REPO + "/releases/tag/" + args.version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--version", default="")
    p = commands.add_parser("package")
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--built", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--arch", choices=ARCHES, required=True)
    p.add_argument("--builder", required=True)
    for name in ["collect", "publish"]:
        p = commands.add_parser(name)
        p.add_argument("--assets", type=Path, required=True)
        p.add_argument("--version", required=True)
        p.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "package": package, "collect": collect, "publish": publish}[args.command](args)


if __name__ == "__main__":
    main()
