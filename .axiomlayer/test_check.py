#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check  # noqa: E402


class IntegrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pins = check.load_pins()

    def test_complete_static_contract(self) -> None:
        check.verify(False)

    def test_floating_action_is_refused(self) -> None:
        with self.assertRaisesRegex(check.ContractError, "full commit SHA"):
            check.validate_action_references(
                "steps:\n  - uses: actions/checkout@v4\n",
                Path("floating.yml"),
                self.pins["actions"],
            )

    def test_unreviewed_action_commit_is_refused(self) -> None:
        with self.assertRaisesRegex(check.ContractError, "unreviewed action revision"):
            check.validate_action_references(
                "steps:\n  - uses: actions/checkout@" + "0" * 40 + "\n",
                Path("unreviewed.yml"),
                self.pins["actions"],
            )

    def test_unguarded_upstream_job_is_refused(self) -> None:
        with self.assertRaisesRegex(check.ContractError, "lacks its repository guard"):
            check.verify_upstream_job_guards(
                Path(".github/workflows/update-website.yml"),
                "jobs:\n  build:\n    runs-on: macos-14\n  deploy:\n    runs-on: ubuntu-24.04\n",
            )

    def test_locked_archive_materializes_exact_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="axiom-nix-darwin-test-") as temporary:
            destination = Path(temporary) / "source"
            revision = check.safe_extract_git_archive(
                self.pins["sourceCommit"], destination
            )
            self.assertEqual(revision, self.pins["sourceCommit"])
            with (destination / "version.json").open(encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), self.pins["releaseMetadata"])

    def test_release_script_refuses_in_axiomlayer_fork(self) -> None:
        result = subprocess.run(
            ["bash", "scripts/release.sh"],
            cwd=check.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing outside", result.stderr)


if __name__ == "__main__":
    unittest.main()
