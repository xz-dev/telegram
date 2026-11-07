import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from release import collect, publish, valid_version


class ReleaseAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-assets-", dir="/var/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(assets=self.root, version="v0.2610.0-xz.test", source_sha="a" * 40)
        for arch in ["amd64", "arm64"]:
            data = b"native binary fixture " + arch.encode()
            info = {"version": self.args.version, "source_commit": self.args.source_sha,
                    "platform": "linux/" + arch, "inputs": {"ci": "b" * 40},
                    "binary_sha256": hashlib.sha256(data).hexdigest()}
            (self.root / ("build-info-linux-" + arch + ".json")).write_text(json.dumps(info))
            with tarfile.open(self.root / ("mautrix-telegram-linux-" + arch + ".tar.gz"), "w:gz") as archive:
                for name, contents in {"mautrix-telegram": data, "build-info.json": json.dumps(info).encode(),
                                       "README.md": b"usage", "LICENSE": b"license", "LICENSE.exceptions": b"exceptions"}.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(contents)
                    archive.addfile(member, io.BytesIO(contents))

    def test_collect_accepts_matching_native_builds_and_records_source(self):
        collect(self.args)
        info = json.loads((self.root / "build-info.json").read_text())
        self.assertEqual(info["source_commit"], self.args.source_sha)
        self.assertEqual({b["platform"] for b in info["builds"]}, {"linux/amd64", "linux/arm64"})

    def test_different_architecture_source_is_rejected(self):
        path = self.root / "build-info-linux-arm64.json"
        info = json.loads(path.read_text())
        info["source_commit"] = "c" * 40
        path.write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError, "does not match"):
            collect(self.args)
        self.assertFalse((self.root / "build-info.json").exists())

    def test_tampered_package_metadata_is_rejected(self):
        path = self.root / "build-info-linux-amd64.json"
        info = json.loads(path.read_text())
        info["binary_sha256"] = "f" * 64
        path.write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError, "archive binary"):
            collect(self.args)

    def test_publication_checks_canonical_repository_url_before_tag_collision(self):
        urls = []
        def open_api(request):
            urls.append(request.full_url)
            self.assertEqual(request.headers["Authorization"], "Bearer fixture-token")
            if len(urls) == 1:
                self.assertEqual(request.full_url, "https://api.github.com/repos/xz-dev/telegram")
                return io.BytesIO(b'{"full_name":"xz-dev/telegram"}')
            return io.BytesIO(b'{"ref":"refs/tags/existing"}')
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "xz-dev/telegram", "GH_TOKEN": "fixture-token"}), \
                patch("release.urllib.request.urlopen", side_effect=open_api), \
                patch("release.run") as mutate:
            with self.assertRaisesRegex(ValueError, "release/tag already exists"):
                publish(self.args)
            mutate.assert_not_called()
        self.assertEqual(urls, ["https://api.github.com/repos/xz-dev/telegram",
                               "https://api.github.com/repos/xz-dev/telegram/git/ref/tags/" + self.args.version])

    def test_version_is_downstream_and_not_a_shell_or_tag_injection(self):
        self.assertEqual(valid_version(self.args.version), self.args.version)
        for version in ["v0.2610.0", "v0.2610.0-xz.test;rm -rf /", "--force", "../../tag", "v0.2610.0-xz."]:
            with self.assertRaises(ValueError):
                valid_version(version)
