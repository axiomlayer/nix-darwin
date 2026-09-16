#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check  # noqa: E402


def write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def write_linux_x64_uname(fake_bin: Path) -> None:
    write_executable(
        fake_bin / "uname",
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  -s) printf '%s\\n' Linux ;;\n"
        "  -m) printf '%s\\n' x86_64 ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
    )


class IntegrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pins = check.load_pins()

    def integration_workflow(self) -> str:
        return (
            check.ROOT / ".github/workflows/axiomlayer-integration.yml"
        ).read_text(encoding="utf-8")

    def test_complete_static_contract(self) -> None:
        check.verify(False)

    def test_expected_workflow_contexts_are_allowed(self) -> None:
        accepted = (
            (
                "pull_request",
                "refs/pull/41/merge",
                check.CANONICAL_BRANCH,
                "",
                False,
            ),
            ("push", check.CANONICAL_REF, "", "", True),
            (
                "schedule",
                check.CANONICAL_REF,
                "",
                check.CANONICAL_WORKFLOW_REF,
                True,
            ),
            (
                "workflow_dispatch",
                check.CANONICAL_REF,
                "",
                check.CANONICAL_WORKFLOW_REF,
                True,
            ),
        )
        for event_name, ref, base_ref, workflow_ref, ref_protected in accepted:
            with self.subTest(event_name=event_name):
                self.assertTrue(
                    check.workflow_context_allowed(
                        check.CANONICAL_REPOSITORY,
                        event_name,
                        ref,
                        base_ref,
                        workflow_ref,
                        ref_protected,
                    )
                )

    def test_owner_casing_is_refused(self) -> None:
        uppercase_repository = "".join(("Axiom", "Layer", "/nix-darwin"))
        self.assertFalse(
            check.workflow_context_allowed(
                uppercase_repository,
                "push",
                check.CANONICAL_REF,
                ref_protected=True,
            )
        )
        with self.assertRaisesRegex(check.ContractError, "exact lowercase"):
            check.verify_machine_identity_text(
                '{"repository": "' + uppercase_repository + '"}',
                Path("uppercase.json"),
            )
        for namespace in ("AXIOMLAYER/", "axiomLayer/"):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(
                check.ContractError, "exact lowercase"
            ):
                check.verify_machine_identity_text(
                    namespace + "nix-darwin", Path("mixed-case.txt")
                )

        workflow = self.integration_workflow()
        candidate = workflow.replace(
            check.CANONICAL_REPOSITORY, uppercase_repository, 1
        )
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "exact lowercase"):
            check.verify_integration_workflow(candidate)

    def test_pull_requests_require_master_merge_refs(self) -> None:
        refused = (
            ("refs/pull/41/head", check.CANONICAL_BRANCH),
            ("refs/pull/0/merge", check.CANONICAL_BRANCH),
            ("refs/pull/topic/merge", check.CANONICAL_BRANCH),
            ("refs/pull/41/merge", "feature"),
            (check.CANONICAL_REF, check.CANONICAL_BRANCH),
        )
        for ref, base_ref in refused:
            with self.subTest(ref=ref, base_ref=base_ref):
                self.assertFalse(
                    check.workflow_context_allowed(
                        check.CANONICAL_REPOSITORY,
                        "pull_request",
                        ref,
                        base_ref,
                    )
                )

    def test_feature_manual_ref_is_refused(self) -> None:
        self.assertFalse(
            check.workflow_context_allowed(
                check.CANONICAL_REPOSITORY,
                "workflow_dispatch",
                "refs/heads/feature/harden-me",
                workflow_ref=check.CANONICAL_WORKFLOW_REF,
                ref_protected=True,
            )
        )
        workflow = self.integration_workflow()
        candidate = workflow.replace(
            "github.ref == 'refs/heads/master'",
            "github.ref == 'refs/heads/feature/harden-me'",
            1,
        )
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "exact repository"):
            check.verify_integration_workflow(candidate)

    def test_unprotected_master_is_refused(self) -> None:
        for event_name, workflow_ref in (
            ("push", ""),
            ("schedule", check.CANONICAL_WORKFLOW_REF),
            ("workflow_dispatch", check.CANONICAL_WORKFLOW_REF),
        ):
            with self.subTest(event_name=event_name):
                self.assertFalse(
                    check.workflow_context_allowed(
                        check.CANONICAL_REPOSITORY,
                        event_name,
                        check.CANONICAL_REF,
                        workflow_ref=workflow_ref,
                        ref_protected=False,
                    )
                )
        workflow = self.integration_workflow()
        candidate = workflow.replace(
            "github.ref_protected == true", "github.ref_protected == false", 1
        )
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "exact repository"):
            check.verify_integration_workflow(candidate)

    def test_alternate_manual_workflow_identity_is_refused(self) -> None:
        alternate = (
            "axiomlayer/nix-darwin/.github/workflows/"
            "alternate.yml@refs/heads/master"
        )
        for event_name in check.PROACTIVE_EVENTS:
            with self.subTest(event_name=event_name):
                self.assertFalse(
                    check.workflow_context_allowed(
                        check.CANONICAL_REPOSITORY,
                        event_name,
                        check.CANONICAL_REF,
                        workflow_ref=alternate,
                        ref_protected=True,
                    )
                )
        workflow = self.integration_workflow()
        candidate = workflow.replace(check.CANONICAL_WORKFLOW_REF, alternate, 1)
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "exact repository"):
            check.verify_integration_workflow(candidate)

    def test_unapproved_event_is_refused(self) -> None:
        self.assertFalse(
            check.workflow_context_allowed(
                check.CANONICAL_REPOSITORY,
                "pull_request_target",
                check.CANONICAL_REF,
                check.CANONICAL_BRANCH,
                check.CANONICAL_WORKFLOW_REF,
                True,
            )
        )

    def test_trigger_set_and_daily_schedule_are_exact(self) -> None:
        workflow = self.integration_workflow()
        check.verify_integration_trigger(workflow)
        candidate = workflow.replace(
            f'cron: "{check.DAILY_CRON}"', 'cron: "23 7 * * 1"', 1
        )
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "trigger contract"):
            check.verify_integration_workflow(candidate)

    def test_dated_hosted_runner_inventory_is_exact(self) -> None:
        workflow = self.integration_workflow()
        check.verify_hosted_runner_contract(workflow)
        mutations = (
            ("ubuntu-24.04", "ubuntu-latest"),
            ("macos-15-intel", "self-hosted"),
            ("runs-on: ubuntu-24.04", "runs-on:\n      group: private-fleet"),
        )
        for expected, replacement in mutations:
            with self.subTest(replacement=replacement):
                candidate = workflow.replace(expected, replacement, 1)
                self.assertNotEqual(candidate, workflow)
                with self.assertRaises(check.ContractError):
                    check.verify_integration_workflow(candidate)

    def test_dirty_checkout_is_refused(self) -> None:
        workflow = self.integration_workflow()
        candidate = workflow.replace("          clean: true\n", "", 1)
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "clean: true"):
            check.verify_integration_workflow(candidate)

    def test_true_fork_checkouts_require_full_history(self) -> None:
        workflow = self.integration_workflow()
        candidate = workflow.replace("          fetch-depth: 0\n", "", 1)
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "fetch-depth: 0"):
            check.verify_integration_workflow(candidate)

    def test_checkout_credentials_are_refused(self) -> None:
        workflow = self.integration_workflow()
        replacements = (
            "",
            "persist-credentials: true",
            "persist-credentials: false\n          persist-credentials: true",
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                candidate = workflow.replace(
                    "persist-credentials: false", replacement, 1
                )
                self.assertNotEqual(candidate, workflow)
                with self.assertRaisesRegex(
                    check.ContractError, "persist-credentials: false"
                ):
                    check.verify_integration_workflow(candidate)

    def test_quarantined_checkouts_are_clean_and_credential_free(self) -> None:
        path = Path(".github/upstream-workflows/test.yml.disabled")
        workflow = (check.ROOT / path).read_text(encoding="utf-8")
        self.assertEqual(check.verify_checkout_contract(workflow, path), 3)
        candidate = workflow.replace("        clean: true", "        clean: false", 1)
        with self.assertRaisesRegex(check.ContractError, "clean: true"):
            check.verify_checkout_contract(candidate, path)

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

    def test_container_action_is_refused_outside_allowlist(self) -> None:
        with self.assertRaisesRegex(
            check.ContractError, "outside the reviewed allowlist"
        ):
            check.validate_action_references(
                "steps:\n  - uses: docker://example.invalid/tool@sha256:"
                + "0" * 64
                + "\n",
                Path("container.yml"),
                self.pins["actions"],
            )

    def test_unguarded_upstream_job_is_refused(self) -> None:
        with self.assertRaisesRegex(check.ContractError, "lacks its repository guard"):
            check.verify_job_guard(
                "jobs:\n  build:\n    runs-on: macos-14\n  deploy:\n    runs-on: ubuntu-24.04\n",
                "build",
                "nix-darwin/nix-darwin",
            )

    def test_quarantined_pages_workflow_cannot_be_reactivated(self) -> None:
        reactivated = set(check.EXPECTED_WORKFLOWS) | {
            Path(".github/workflows/update-website.yml")
        }
        with self.assertRaisesRegex(check.ContractError, "workflow inventory changed"):
            check.verify_workflow_inventory(
                reactivated, set(check.EXPECTED_QUARANTINE_FILES)
            )

    def test_integration_cannot_delegate_nix_bootstrap(self) -> None:
        workflow = (
            check.ROOT / ".github/workflows/axiomlayer-integration.yml"
        ).read_text(encoding="utf-8")
        candidate = workflow.replace(
            "run: sh .axiomlayer/install-nix-ci.sh",
            "uses: cachix/install-nix-action@" + "0" * 40,
            1,
        )
        with self.assertRaisesRegex(check.ContractError, "must not delegate"):
            check.verify_integration_workflow(candidate)

    def test_integration_cannot_pass_automatic_token(self) -> None:
        workflow = (
            check.ROOT / ".github/workflows/axiomlayer-integration.yml"
        ).read_text(encoding="utf-8")
        candidate = workflow.replace(
            "run: sh .axiomlayer/install-nix-ci.sh",
            "run: sh .axiomlayer/install-nix-ci.sh\n"
            "        env:\n"
            "          GH_TOKEN: ${{ github.token }}",
            1,
        )
        with self.assertRaisesRegex(check.ContractError, "forbidden capability"):
            check.verify_integration_workflow(candidate)

    def test_integration_cannot_select_an_environment(self) -> None:
        workflow = (
            check.ROOT / ".github/workflows/axiomlayer-integration.yml"
        ).read_text(encoding="utf-8")
        candidate = workflow.replace(
            "    runs-on: ubuntu-24.04",
            "    environment: production\n    runs-on: ubuntu-24.04",
            1,
        )
        with self.assertRaisesRegex(check.ContractError, "forbidden capability"):
            check.verify_integration_workflow(candidate)

    def test_integration_rejects_secrets_writes_publishing_releases_and_sync(self) -> None:
        workflow = self.integration_workflow()
        mutations = {
            "secret": workflow.replace(
                "          CONTRACT_RESULT: ${{ needs.contract.result }}",
                "          REAL_TOKEN: ${{ secrets ['REAL_TOKEN'] }}\n"
                "          CONTRACT_RESULT: ${{ needs.contract.result }}",
                1,
            ),
            "write permission": workflow.replace(
                "  contents: read", "  contents: write", 1
            ),
            "publishing": workflow.replace(
                '          test "$CONTRACT_RESULT" = success',
                "          nix copy --to https://cache.invalid .#payload\n"
                '          test "$CONTRACT_RESULT" = success',
                1,
            ),
            "release": workflow.replace(
                '          test "$CONTRACT_RESULT" = success',
                "          gh release create unsafe\n"
                '          test "$CONTRACT_RESULT" = success',
                1,
            ),
            "synchronization": workflow.replace(
                '          test "$CONTRACT_RESULT" = success',
                "          git pull --ff-only upstream master\n"
                '          test "$CONTRACT_RESULT" = success',
                1,
            ),
        }
        for capability, candidate in mutations.items():
            with self.subTest(capability=capability):
                self.assertNotEqual(candidate, workflow)
                with self.assertRaises(check.ContractError):
                    check.verify_integration_workflow(candidate)

    def test_integration_owner_guard_cannot_be_bypassed(self) -> None:
        workflow = self.integration_workflow()
        candidate = workflow.replace(
            "github.repository == 'axiomlayer/nix-darwin' &&",
            "github.repository == 'axiomlayer/nix-darwin' || true ||",
            1,
        )
        self.assertNotEqual(candidate, workflow)
        with self.assertRaisesRegex(check.ContractError, "exact repository"):
            check.verify_integration_workflow(candidate)

    def test_nix_bootstrap_byte_tamper_is_refused(self) -> None:
        source = check.INTEGRATION / "install-nix-ci.sh"
        with tempfile.TemporaryDirectory(prefix="axiom-nix-bootstrap-test-") as root:
            candidate = Path(root) / "install-nix-ci.sh"
            candidate.write_bytes(source.read_bytes() + b"\n# drift\n")
            candidate.chmod(0o755)
            with self.assertRaisesRegex(check.ContractError, "repository-pinned"):
                check.verify_nix_bootstrap(candidate, self.pins["nix"])

    def test_nix_bootstrap_second_pin_table_blocks_coordinated_tamper(self) -> None:
        source = check.INTEGRATION / "install-nix-ci.sh"
        with tempfile.TemporaryDirectory(prefix="axiom-nix-bootstrap-test-") as root:
            candidate = Path(root) / "install-nix-ci.sh"
            candidate.write_bytes(source.read_bytes() + b"\n# coordinated drift\n")
            candidate.chmod(0o755)
            forged_pin = json.loads(json.dumps(self.pins["nix"]))
            forged_pin["wrapperSha256"] = hashlib.sha256(
                candidate.read_bytes()
            ).hexdigest()
            with self.assertRaisesRegex(check.ContractError, "independently reviewed"):
                check.verify_nix_bootstrap(candidate, forged_pin)

    def test_nix_bootstrap_cannot_reference_live_token(self) -> None:
        source = (check.INTEGRATION / "install-nix-ci.sh").read_text(encoding="utf-8")
        with self.assertRaisesRegex(check.ContractError, "credential surface"):
            check.verify_nix_bootstrap_source(
                source + "\n# GITHUB_TOKEN\n", check.REVIEWED_NIX_BOOTSTRAP
            )

    def test_nix_bootstrap_requires_empty_environment_boundary(self) -> None:
        source = (check.INTEGRATION / "install-nix-ci.sh").read_text(encoding="utf-8")
        with self.assertRaisesRegex(check.ContractError, "lost required boundary"):
            check.verify_nix_bootstrap_source(
                source.replace("env -i", "env", 1), check.REVIEWED_NIX_BOOTSTRAP
            )

    def test_downloaded_launcher_tamper_stops_before_execution(self) -> None:
        wrapper = check.INTEGRATION / "install-nix-ci.sh"
        with tempfile.TemporaryDirectory(prefix="axiom-nix-tamper-test-") as root:
            temporary = Path(root)
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            launcher_ran = temporary / "launcher-ran"
            write_linux_x64_uname(fake_bin)
            write_executable(
                fake_bin / "curl",
                "#!/bin/sh\n"
                "set -eu\n"
                "output=\n"
                'while [ "$#" -gt 0 ]; do\n'
                '  case "$1" in\n'
                "    --output) output=$2; shift 2 ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "printf '%s\\n' 'tampered launcher' > \"$output\"\n",
            )
            write_executable(
                fake_bin / "sh",
                f"#!/bin/sh\nprintf ran > {shlex.quote(str(launcher_ran))}\n",
            )
            environment = os.environ.copy()
            environment["PATH"] = (
                str(fake_bin) + os.pathsep + environment.get("PATH", os.defpath)
            )
            environment["TMPDIR"] = str(temporary)
            result = subprocess.run(
                ["/bin/sh", str(wrapper)],
                cwd=check.ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("installer digest drift", result.stderr)
            self.assertFalse(launcher_ran.exists())

    def test_wrong_platform_digest_stops_before_execution(self) -> None:
        wrapper = check.INTEGRATION / "install-nix-ci.sh"
        with tempfile.TemporaryDirectory(prefix="axiom-nix-platform-test-") as root:
            temporary = Path(root)
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            launcher_ran = temporary / "launcher-ran"
            write_linux_x64_uname(fake_bin)
            write_executable(
                fake_bin / "curl",
                "#!/bin/sh\n"
                "set -eu\n"
                "output=\n"
                'while [ "$#" -gt 0 ]; do\n'
                '  case "$1" in\n'
                "    --output) output=$2; shift 2 ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "printf '%s\\n' 'hash=wrong-platform-digest' > \"$output\"\n",
            )
            write_executable(
                fake_bin / "shasum",
                "#!/bin/sh\n"
                f"printf '%s  %s\\n' '{self.pins['nix']['installerSha256']}' \"$3\"\n",
            )
            write_executable(
                fake_bin / "sh",
                f"#!/bin/sh\nprintf ran > {shlex.quote(str(launcher_ran))}\n",
            )
            environment = os.environ.copy()
            environment["PATH"] = (
                str(fake_bin) + os.pathsep + environment.get("PATH", os.defpath)
            )
            environment["TMPDIR"] = str(temporary)
            result = subprocess.run(
                ["/bin/sh", str(wrapper)],
                cwd=check.ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("lacks expected platform digest", result.stderr)
            self.assertFalse(launcher_ran.exists())

    def test_nix_launcher_runtime_environment_is_scrubbed(self) -> None:
        wrapper = check.INTEGRATION / "install-nix-ci.sh"
        with tempfile.TemporaryDirectory(prefix="axiom-nix-env-test-") as root:
            temporary = Path(root)
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            captured_environment = temporary / "launcher.env"

            write_linux_x64_uname(fake_bin)
            platform_digest = self.pins["nix"]["binaryTarballSha256"]["x86_64-linux"]
            write_executable(
                fake_bin / "curl",
                "#!/bin/sh\n"
                "set -eu\n"
                "output=\n"
                'while [ "$#" -gt 0 ]; do\n'
                '  case "$1" in\n'
                "    --output) output=$2; shift 2 ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                'test -n "$output"\n'
                f"printf '%s\\n' 'hash={platform_digest}' > \"$output\"\n",
            )
            write_executable(
                fake_bin / "shasum",
                "#!/bin/sh\n"
                f"printf '%s  %s\\n' '{self.pins['nix']['installerSha256']}' \"$3\"\n",
            )
            write_executable(
                fake_bin / "sh",
                "#!/bin/sh\n"
                f"/usr/bin/env > {shlex.quote(str(captured_environment))}\n"
                "exit 73\n",
            )

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(fake_bin)
                    + os.pathsep
                    + environment.get("PATH", os.defpath),
                    "TMPDIR": str(temporary),
                    "GITHUB_TOKEN": "poison-github-token",
                    "GH_TOKEN": "poison-gh-token",
                    "ACTIONS_RUNTIME_TOKEN": "poison-actions-token",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "poison-oidc-token",
                    "ACTIONS_RESULTS_URL": "https://results.invalid",
                    "GITHUB_ENV": str(temporary / "poison-github-env"),
                    "GITHUB_PATH": str(temporary / "poison-github-path"),
                    "NIX_CONFIG": "poison-nix-config",
                    "BASH_ENV": "poison-shell-startup",
                    "HTTPS_PROXY": "https://credential.invalid",
                    "FLEET_POISON": "must-not-cross",
                }
            )
            result = subprocess.run(
                ["/bin/sh", str(wrapper)],
                cwd=check.ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 73, result.stderr)
            observed = dict(
                line.split("=", 1)
                for line in captured_environment.read_text(
                    encoding="utf-8"
                ).splitlines()
                if "=" in line
            )
            self.assertEqual(observed["CI"], "true")
            self.assertNotEqual(observed["HOME"], environment.get("HOME"))
            for name in (
                "GITHUB_TOKEN",
                "GH_TOKEN",
                "ACTIONS_RUNTIME_TOKEN",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                "ACTIONS_RESULTS_URL",
                "GITHUB_ENV",
                "GITHUB_PATH",
                "NIX_CONFIG",
                "BASH_ENV",
                "HTTPS_PROXY",
                "FLEET_POISON",
            ):
                self.assertNotIn(name, observed)

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
