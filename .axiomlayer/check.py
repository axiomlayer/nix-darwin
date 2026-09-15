#!/usr/bin/env python3
"""Fail-closed checks for the AxiomLayer nix-darwin integration fork."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / ".axiomlayer"
PINS_FILE = INTEGRATION / "pins.json"
FIXTURE = INTEGRATION / "fleet-fixture"
EXPECTED_WORKFLOWS = {
    Path(".github/workflows/axiomlayer-integration.yml"),
}
QUARANTINED_UPSTREAM_WORKFLOWS = {
    Path(".github/upstream-workflows/test.yml.disabled"): (
        "test-stable",
        "install-against-stable",
        "install-flake",
    ),
    Path(".github/upstream-workflows/update-website.yml.disabled"): (
        "build",
        "deploy",
    ),
}
EXPECTED_QUARANTINE_FILES = set(QUARANTINED_UPSTREAM_WORKFLOWS) | {
    Path(".github/upstream-workflows/README.md"),
}
INTEGRATION_JOBS = (
    "contract",
    "darwin-evaluation",
    "darwin-native-build",
    "promotion-gate",
)
CANONICAL_REPOSITORY = "axiomlayer/nix-darwin"
CANONICAL_BRANCH = "master"
CANONICAL_REF = "refs/heads/master"
CANONICAL_WORKFLOW_REF = (
    "axiomlayer/nix-darwin/.github/workflows/"
    "axiomlayer-integration.yml@refs/heads/master"
)
PROACTIVE_EVENTS = ("schedule", "workflow_dispatch")
DAILY_CRON = "23 7 * * *"
INTEGRATION_CONTEXT_GUARD = (
    "github.repository == 'axiomlayer/nix-darwin' && "
    "((github.event_name == 'pull_request' && github.base_ref == 'master' && "
    "startsWith(github.ref, 'refs/pull/') && endsWith(github.ref, '/merge')) || "
    "(github.event_name == 'push' && github.ref == 'refs/heads/master' && "
    "github.ref_protected == true) || "
    "((github.event_name == 'schedule' || "
    "github.event_name == 'workflow_dispatch') && "
    "github.ref == 'refs/heads/master' && github.ref_protected == true && "
    "github.workflow_ref == 'axiomlayer/nix-darwin/.github/workflows/"
    "axiomlayer-integration.yml@refs/heads/master'))"
)
EXPECTED_INTEGRATION_JOB_CONDITIONS = {
    "contract": INTEGRATION_CONTEXT_GUARD,
    "darwin-evaluation": INTEGRATION_CONTEXT_GUARD,
    "darwin-native-build": INTEGRATION_CONTEXT_GUARD,
    "promotion-gate": f"always() && ({INTEGRATION_CONTEXT_GUARD})",
}
EXPECTED_INTEGRATION_TRIGGER = f'''on:
  pull_request:
    branches:
      - master
  push:
    branches:
      - master
  schedule:
    - cron: "{DAILY_CRON}"
  workflow_dispatch:'''
EXPECTED_JOB_RUNNERS = (
    "ubuntu-24.04",
    "ubuntu-24.04",
    "${{ matrix.runner }}",
    "ubuntu-24.04",
)
EXPECTED_MATRIX_RUNNERS = ("macos-15", "macos-15-intel")
REVIEWED_NIX_BOOTSTRAP = {
    "version": "2.35.2",
    "installUrl": "https://releases.nixos.org/nix/nix-2.35.2/install",
    "installerSha256": "9adda97297d9e8ab360df95c729eabff4f4f93d6db091953c3a68f29e3fb130c",
    "wrapperSha256": "32f1d65f344e07584dfa0f6573395b1a9e2b6ec99d39e6fa461a790ff867436a",
    "binaryTarballSha256": {
        "aarch64-darwin": "1695c13aba5afa7c2ecd6dc4a9393f602e7bbc440ed45e81602c831546580ec3",
        "x86_64-darwin": "d725518d89f3b0b8d4af702a9d38d519814014cbe125afb3ed0545c9d755f6a5",
        "aarch64-linux": "4d0302a2910f5eec1c33b8deef634f04899a75737e7001ec49908d003ae5efda",
        "x86_64-linux": "0c3960a9792331a22081c3c7a5d8465db9b17c50b3acdf18587fa4c6f2cb1158",
    },
}
REVIEWED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/deploy-pages": "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e",
    "actions/upload-pages-artifact": "56afc609e74202658d3ffba0e8f6dda462b719fa",
    "cachix/install-nix-action": "13d8dd58da0234aa297dedd986986ccb8e7f3e24",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
NAR_HASH = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
ACTION_REFERENCE = re.compile(
    r"^[ \t]*(?:-[ \t]*)?uses[ \t]*:[ \t]*([^ \t\r\n#]+)", re.MULTILINE
)
ACTION_KEY = re.compile(r"^[ \t]*(?:-[ \t]*)?uses[ \t]*:", re.MULTILINE)
LIST_ITEM = re.compile(r"^(?P<indent> *)-\s+")
CHECKOUT_OPTION = re.compile(
    r"^(?P<indent> *)"
    r"(?P<key>clean|fetch-depth|persist-credentials):"
    r"\s*(?P<value>[^\s#]+)\s*(?:#.*)?$"
)
CHECKOUT_OVERRIDE = re.compile(
    r"^\s*(?:repository|ref|token|ssh-key|ssh-known-hosts|github-server-url):",
    re.IGNORECASE,
)


class ContractError(RuntimeError):
    """Raised when a fork integration invariant is not satisfied."""


def refuse(message: str) -> None:
    raise ContractError(message)


def load_pins() -> dict:
    with PINS_FILE.open(encoding="utf-8") as stream:
        return json.load(stream)


def run(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        capture_output=capture,
        text=True,
    )


def verify_pins(pins: dict) -> None:
    exact = {
        "schema": "axiomlayer-nix-darwin-integration-v1",
        "upstream": "nix-darwin/nix-darwin",
        "fork": CANONICAL_REPOSITORY,
        "version": "nix-darwin-26.05",
        "releaseBranch": "nix-darwin-26.05",
        "sourceCommit": "c3e90c89649b07d1a96e4b9dd6cd0d6e44b91a74",
    }
    for key, expected in exact.items():
        if pins.get(key) != expected:
            refuse(f"{key} must be {expected!r}, got {pins.get(key)!r}")

    archive = pins.get("sourceArchive", {})
    if archive.get("url") != (
        "https://codeload.github.com/axiomlayer/nix-darwin/tar.gz/"
        + pins["sourceCommit"]
    ):
        refuse("sourceArchive.url must be the exact AxiomLayer fork commit")
    expected_promotion_url = (
        "https://install.axiomlayer.com/nix-inputs/nix-darwin/"
        + pins["sourceCommit"]
        + "/source.tar.gz"
    )
    if archive.get("promotionUrl") != expected_promotion_url:
        refuse("sourceArchive.promotionUrl drifted from the promotion contract")
    if not SHA256.fullmatch(archive.get("sha256", "")):
        refuse("source archive is not pinned by a full SHA-256")
    if not NAR_HASH.fullmatch(archive.get("narHash", "")):
        refuse("source archive is not pinned by a NAR hash")

    if pins.get("releaseMetadata") != {
        "release": "26.05",
        "isReleaseBranch": True,
    }:
        refuse("release metadata no longer identifies the 26.05 release branch")

    consumers = pins.get("fleetConsumers", {})
    expected_consumers = {
        "dotfiles": (
            "axiomlayer/dotfiles",
            "ee9c1a27542f7918a30d9e9e33155baea2b96c8a",
            49,
            "nix/checks/evaluation.nix",
        ),
        "margay": (
            "axiomlayer/margay",
            "da44c4cbf434606fc58ae8028897dd520ab40afb",
            8,
            "darwinConfigurations.margay",
        ),
    }
    if set(consumers) != set(expected_consumers):
        refuse("the exact fleet consumer set changed")
    for name, expected in expected_consumers.items():
        consumer = consumers[name]
        observed = (
            consumer.get("repository"),
            consumer.get("revision"),
            consumer.get("pullRequest"),
            consumer.get("surface"),
        )
        if observed != expected:
            refuse(f"{name} consumer provenance changed: {observed!r}")
        if not GIT_SHA.fullmatch(consumer["revision"]):
            refuse(f"{name} consumer revision is not immutable")

    if pins.get("nix") != REVIEWED_NIX_BOOTSTRAP:
        refuse("the repository and reviewer Nix bootstrap pin tables disagree")

    nixpkgs = pins.get("nixpkgs", {})
    if nixpkgs.get("upstream") != "NixOS/nixpkgs":
        refuse("nixpkgs provenance changed")
    if nixpkgs.get("revision") != "c3eea5b2156db11c7eeeada3dc737711255b253e":
        refuse("nixpkgs must match the dotfiles promotion authority")
    if not NAR_HASH.fullmatch(nixpkgs.get("narHash", "")):
        refuse("nixpkgs is missing its exact NAR hash")

    if pins.get("systems") != ["aarch64-darwin", "x86_64-darwin"]:
        refuse("both native Darwin architectures must remain in the gate")

    actions = pins.get("actions", {})
    if actions != REVIEWED_ACTIONS:
        refuse("the reviewed Action commit allowlist changed")
    for action, revision in actions.items():
        if not GIT_SHA.fullmatch(revision):
            refuse(f"{action} is not pinned to a full commit SHA")

    expected_acceptance = {
        "margay-activation",
        "margay-self-runner-enrollment",
        "margay-cold-boot-resume",
        "margay-gui-rendering",
        "margay-homebrew-and-mas-convergence",
    }
    if set(pins.get("hostAcceptance", [])) != expected_acceptance:
        refuse("Margay host-only acceptance boundaries changed")


def workflow_files() -> set[Path]:
    directory = ROOT / ".github" / "workflows"
    if directory.is_symlink() or not directory.is_dir():
        refuse("the executable workflow directory must be a local directory")
    return {
        path.relative_to(ROOT)
        for path in directory.rglob("*")
        if (path.is_file() or path.is_symlink())
        and path.suffix.lower() in {".yml", ".yaml"}
    }


def quarantine_files() -> set[Path]:
    directory = ROOT / ".github" / "upstream-workflows"
    if directory.is_symlink() or not directory.is_dir():
        return set()
    return {
        path.relative_to(ROOT)
        for path in directory.iterdir()
        if path.is_file() or path.is_symlink()
    }


def verify_workflow_inventory(found: set[Path], quarantined: set[Path]) -> None:
    if found != EXPECTED_WORKFLOWS:
        refuse(f"workflow inventory changed: {sorted(map(str, found))}")
    if quarantined != EXPECTED_QUARANTINE_FILES:
        refuse(f"upstream workflow quarantine changed: {sorted(map(str, quarantined))}")


def validate_action_references(text: str, path: Path, actions: dict) -> set[str]:
    seen: set[str] = set()
    references = list(ACTION_REFERENCE.finditer(text))
    if len(ACTION_KEY.findall(text)) != len(references):
        refuse(f"{path}: workflow contains a malformed Action reference")
    for match in references:
        reference = match.group(1)
        if reference.startswith("./"):
            refuse(f"{path}: local Actions are outside the reviewed allowlist")
        if reference.startswith("docker://"):
            if not re.search(r"@sha256:[0-9a-f]{64}$", reference):
                refuse(f"{path}: container action is not digest-pinned: {reference}")
            refuse(f"{path}: container Actions are outside the reviewed allowlist")
        if "@" not in reference:
            refuse(f"{path}: action has no immutable revision: {reference}")
        action, revision = reference.rsplit("@", 1)
        if not GIT_SHA.fullmatch(revision):
            refuse(f"{path}: {action} is not pinned to a full commit SHA")
        if actions.get(action) != revision:
            refuse(f"{path}: unreviewed action revision {action}@{revision}")
        seen.add(action)
    return seen


def action_step_block(lines: list[str], index: int, path: Path) -> tuple[int, list[str]]:
    uses_indent = len(lines[index]) - len(lines[index].lstrip(" "))
    start: int | None = None
    step_indent = -1
    for candidate in range(index, -1, -1):
        match = LIST_ITEM.match(lines[candidate])
        if match and len(match.group("indent")) <= uses_indent:
            start = candidate
            step_indent = len(match.group("indent"))
            break
    if start is None:
        refuse(f"{path}:{index + 1}: Action reference is not inside a step")

    end = len(lines)
    for candidate in range(start + 1, len(lines)):
        line = lines[candidate]
        if not line.strip():
            continue
        indentation = len(line) - len(line.lstrip(" "))
        list_match = LIST_ITEM.match(line)
        if (list_match and indentation <= step_indent) or indentation < step_indent:
            end = candidate
            break
    property_indent = (
        uses_indent + 2
        if lines[index].lstrip().startswith("- uses:")
        else uses_indent
    )
    return property_indent, lines[start:end]


def verify_checkout_contract(
    text: str,
    path: Path,
    expected_count: int | None = None,
    require_full_history: bool = False,
) -> int:
    lines = text.splitlines()
    checkout_count = 0
    for index, line in enumerate(lines):
        match = ACTION_REFERENCE.match(line)
        if match is None or not match.group(1).startswith("actions/checkout@"):
            continue
        checkout_count += 1
        property_indent, block = action_step_block(lines, index, path)
        with_lines = [
            candidate
            for candidate in block
            if re.fullmatch(rf" {{{property_indent}}}with:\s*(?:#.*)?", candidate)
        ]
        if len(with_lines) != 1:
            refuse(f"{path}:{index + 1}: checkout must declare one with mapping")

        observed: dict[str, list[tuple[int, str]]] = {
            "clean": [],
            "fetch-depth": [],
            "persist-credentials": [],
        }
        for candidate in block:
            option = CHECKOUT_OPTION.fullmatch(candidate)
            if option:
                observed[option.group("key")].append(
                    (len(option.group("indent")), option.group("value"))
                )
            if CHECKOUT_OVERRIDE.match(candidate):
                refuse(
                    f"{path}:{index + 1}: checkout may not override source or authentication"
                )
        required = {
            "clean": "true",
            "persist-credentials": "false",
        }
        if require_full_history:
            required["fetch-depth"] = "0"
        for key, value in required.items():
            if observed[key] != [(property_indent + 2, value)]:
                refuse(
                    f"{path}:{index + 1}: checkout must set {key}: {value} "
                    "exactly once inside with"
                )

    if expected_count is not None and checkout_count != expected_count:
        refuse(
            f"{path}: expected {expected_count} reviewed checkouts, got {checkout_count}"
        )
    return checkout_count


def job_block(text: str, job: str) -> list[str]:
    lines = text.splitlines()
    marker = f"  {job}:"
    try:
        start = lines.index(marker)
    except ValueError:
        refuse(f"workflow is missing job {job!r}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", lines[index]):
            end = index
            break
    return lines[start:end]


def job_condition(text: str, job: str) -> str:
    block = job_block(text, job)
    matches = [
        (index, line.removeprefix("    if:").strip())
        for index, line in enumerate(block)
        if line.startswith("    if:")
    ]
    if len(matches) != 1:
        refuse(f"job {job!r} must have exactly one scalar guard")
    index, first = matches[0]
    condition = [] if first in {">", ">-", "|", "|-"} else [first]
    for continuation in block[index + 1 :]:
        if continuation and len(continuation) - len(continuation.lstrip()) <= 4:
            break
        condition.append(continuation.strip())
    normalized = " ".join(part for part in condition if part)
    if not normalized:
        refuse(f"job {job!r} has an empty guard")
    return normalized


def verify_job_guard(text: str, job: str, repository: str) -> None:
    expected = f"github.repository == '{repository}'"
    try:
        if job_condition(text, job) == expected:
            return
    except ContractError:
        pass
    refuse(f"job {job!r} lacks its repository guard for {repository}")


def verify_upstream_job_guards(path: Path, text: str) -> None:
    for job in QUARANTINED_UPSTREAM_WORKFLOWS[path]:
        verify_job_guard(text, job, "nix-darwin/nix-darwin")


def workflow_context_allowed(
    repository: str,
    event_name: str,
    ref: str,
    base_ref: str = "",
    workflow_ref: str = "",
    ref_protected: bool = False,
) -> bool:
    if repository != CANONICAL_REPOSITORY:
        return False
    if event_name == "pull_request":
        return base_ref == CANONICAL_BRANCH and re.fullmatch(
            r"refs/pull/[1-9][0-9]*/merge", ref
        ) is not None
    if event_name == "push":
        return ref == CANONICAL_REF and ref_protected is True
    if event_name in PROACTIVE_EVENTS:
        return (
            ref == CANONICAL_REF
            and ref_protected is True
            and workflow_ref == CANONICAL_WORKFLOW_REF
        )
    return False


def verify_machine_identity_text(text: str, path: Path) -> None:
    for match in re.finditer(r"axiomlayer/", text, re.IGNORECASE):
        if match.group(0) != "axiomlayer/":
            refuse(f"{path}: machine identity must use the exact lowercase namespace")


def verify_integration_trigger(integration: str) -> None:
    trigger = re.search(
        r"^on:\n.*?(?=^[^\s]|\Z)",
        integration,
        re.MULTILINE | re.DOTALL,
    )
    if trigger is None or trigger.group(0).rstrip() != EXPECTED_INTEGRATION_TRIGGER:
        refuse("AxiomLayer integration trigger contract changed")


def verify_integration_job_guards(integration: str) -> None:
    try:
        jobs_text = integration.split("\njobs:\n", 1)[1]
    except IndexError:
        refuse("AxiomLayer integration lost its jobs mapping")
    observed_jobs = tuple(
        re.findall(r"^  ([A-Za-z0-9_-]+):[ \t]*$", jobs_text, re.MULTILINE)
    )
    if observed_jobs != INTEGRATION_JOBS:
        refuse(f"AxiomLayer integration job inventory changed: {observed_jobs!r}")
    if tuple(EXPECTED_INTEGRATION_JOB_CONDITIONS) != INTEGRATION_JOBS:
        refuse("AxiomLayer job and condition inventories disagree")
    for job, expected in EXPECTED_INTEGRATION_JOB_CONDITIONS.items():
        if job_condition(integration, job) != expected:
            refuse(
                f"AxiomLayer job {job!r} lost its exact repository, event, ref, "
                "protection, and workflow guard"
            )


def verify_hosted_runner_contract(integration: str) -> None:
    runs_on = tuple(
        match.strip()
        for match in re.findall(
            r"^    runs-on:[ \t]*(.*?)[ \t]*$", integration, re.MULTILINE
        )
    )
    if runs_on != EXPECTED_JOB_RUNNERS:
        refuse(f"hosted runner inventory changed: {runs_on!r}")
    matrix_runners = tuple(
        re.findall(
            r"^[ \t]+- runner:[ \t]*([^\s#]+)[ \t]*$",
            integration,
            re.MULTILINE,
        )
    )
    if matrix_runners != EXPECTED_MATRIX_RUNNERS:
        refuse(f"hosted Darwin runner inventory changed: {matrix_runners!r}")
    if re.search(r"\bself-hosted\b", integration, re.IGNORECASE):
        refuse("self-hosted runner selection is forbidden")
    if re.search(
        r"^(?:[ \t]*- runner|[ \t]*runs-on):[ \t]*"
        r"(?:ubuntu|macos|windows)-latest\b",
        integration,
        re.MULTILINE | re.IGNORECASE,
    ):
        refuse("floating runner images are forbidden")


def verify_integration_workflow(integration: str) -> None:
    if "\t" in integration:
        refuse("AxiomLayer integration may not use YAML tabs")
    verify_machine_identity_text(
        integration, Path(".github/workflows/axiomlayer-integration.yml")
    )
    verify_integration_trigger(integration)
    verify_integration_job_guards(integration)
    verify_hosted_runner_contract(integration)
    forbidden_patterns = {
        "secret context": r"(?<![-A-Za-z0-9_])secrets\s*(?:\.|\[|:)",
        "automatic token": (
            r"(?<![-A-Za-z0-9_])github\s*"
            r"(?:\.\s*token|\[\s*['\"]token['\"]\s*\])"
        ),
        "whole GitHub context": r"\btojson\s*\(\s*github\s*\)",
        "deployment environment": r"^\s*environment\s*:",
    }
    for description, pattern in forbidden_patterns.items():
        if re.search(pattern, integration, re.MULTILINE | re.IGNORECASE):
            refuse(
                "AxiomLayer integration contains a forbidden capability: "
                + description
            )
    if not re.search(r"^permissions:\n  contents: read$", integration, re.MULTILINE):
        refuse("AxiomLayer integration permissions must be read-only contents")
    if integration.count("permissions:") != 1:
        refuse("AxiomLayer jobs must not add separate token permissions")
    if "cachix/install-nix-action@" in integration:
        refuse("the integration workflow must not delegate Nix bootstrap to an Action")
    integration_path = Path(".github/workflows/axiomlayer-integration.yml")
    if validate_action_references(
        integration, integration_path, REVIEWED_ACTIONS
    ) != {"actions/checkout"}:
        refuse("AxiomLayer integration must use only reviewed checkout")
    action_references = tuple(
        match.group(1) for match in ACTION_REFERENCE.finditer(integration)
    )
    expected_checkout = "actions/checkout@" + REVIEWED_ACTIONS["actions/checkout"]
    if action_references != (expected_checkout,) * 3:
        refuse(f"AxiomLayer Action inventory changed: {action_references!r}")
    verify_checkout_contract(
        integration,
        integration_path,
        expected_count=3,
        require_full_history=True,
    )
    if integration.count("run: sh .axiomlayer/install-nix-ci.sh") != 2:
        refuse("each Nix job must use the reviewed credential-scrubbed bootstrap")
    if re.search(r"^\s+[A-Za-z-]+:\s*write\s*$", integration, re.MULTILINE):
        refuse("AxiomLayer integration requests write authority")
    for fragment in (
        "id-token: write",
        "contents: write",
        "actions: write",
        "attestations: write",
        "checks: write",
        "deployments: write",
        "discussions: write",
        "issues: write",
        "packages: write",
        "pages: write",
        "pull-requests: write",
        "security-events: write",
        "statuses: write",
        "write-all",
        "environment:",
        "secrets.",
        "secrets[",
        "secrets: inherit",
        "github.token",
        "github['token']",
        'github["token"]',
        "github_access_token",
        "github_token",
        "tojson(github)",
        "actions_runtime_token",
        "actions_id_token_request_token",
        "gh release",
        "git push",
        "git pull",
        "git merge",
        "git rebase",
        "git commit",
        "git tag",
        "rsync ",
        "nix copy",
        "cachix push",
        "cachix_auth_token",
        "attic push",
        "docker push",
        "npm publish",
        "twine upload",
        "nix_secret_key_file",
        "secret-key-files",
        "actions/deploy-pages",
        "actions/upload-pages-artifact",
        "actions/upload-artifact",
        "actions/download-artifact",
        "darwin-rebuild switch",
        "launchctl",
        "osascript",
        "pull_request_target:",
        "repository_dispatch:",
        "workflow_run:",
        "workflow_call:",
    ):
        if fragment in integration.lower():
            refuse(
                f"AxiomLayer integration contains a forbidden capability: {fragment}"
            )
    for fabricated in (
        "fabricated-nix-darwin-enrollment-token",
        "fabricated-nix-darwin-passphrase",
        "fabricated-nix-darwin-encryption-key",
    ):
        if fabricated not in integration:
            refuse("secret-shaped integration inputs must remain explicit fabrications")


def verify_workflows(pins: dict) -> None:
    found = workflow_files()
    quarantined = quarantine_files()
    verify_workflow_inventory(found, quarantined)

    all_seen: set[str] = set()
    reviewed_workflows = found | set(QUARANTINED_UPSTREAM_WORKFLOWS)
    for path in sorted(reviewed_workflows):
        if (ROOT / path).is_symlink():
            refuse(f"{path}: workflow policy files must not be symlinks")
        text = (ROOT / path).read_text(encoding="utf-8")
        lowered = text.lower()
        all_seen.update(validate_action_references(text, path, pins["actions"]))
        verify_checkout_contract(text, path)

        if re.search(r"\bself-hosted\b", text, re.IGNORECASE):
            refuse(f"{path}: self-hosted runner selection is forbidden")
        if re.search(
            r"^\s*runs-on:\s*(?:ubuntu|macos|windows)-latest\b",
            text,
            re.MULTILINE | re.IGNORECASE,
        ):
            refuse(f"{path}: floating runner images are forbidden")

        if path in found and ("secrets." in lowered or "secrets: inherit" in lowered):
            refuse(
                f"{path}: CI must not consume real repository or organization secrets"
            )
        if path in found and "pull_request_target:" in text:
            refuse(f"{path}: privileged pull_request_target execution is forbidden")

        if path in QUARANTINED_UPSTREAM_WORKFLOWS:
            if not text.startswith(
                "# Inert upstream snapshot. GitHub Actions does not load this file from here.\n"
            ):
                refuse(f"{path}: upstream workflow lost its quarantine marker")
            verify_upstream_job_guards(path, text)

    if all_seen != set(pins["actions"]):
        refuse("workflow action use and the reviewed Action allowlist disagree")

    integration = (ROOT / ".github/workflows/axiomlayer-integration.yml").read_text(
        encoding="utf-8"
    )
    verify_integration_workflow(integration)

    website = (
        ROOT / ".github/upstream-workflows/update-website.yml.disabled"
    ).read_text(encoding="utf-8")
    if "pages: write" not in website or "id-token: write" not in website:
        refuse("quarantined upstream Pages snapshot changed and requires review")


def verify_nix_bootstrap_source(source: str, nix_pin: dict) -> None:
    for required in (
        f"NIX_VERSION={nix_pin['version']}",
        f"INSTALLER_URL={nix_pin['installUrl']}",
        f"INSTALLER_SHA256={nix_pin['installerSha256']}",
        "curl --fail --location --proto '=https' --tlsv1.2",
        'actual_installer_sha=$(shasum -a 256 "$installer"',
        'grep -F "hash=$PLATFORM_SHA256" "$installer"',
        "env -i",
        'sh "$installer" --daemon --yes --no-channel-add --no-modify-profile',
    ):
        if required not in source:
            refuse(f"Nix bootstrap lost required boundary: {required}")
    platform_cases = {
        "aarch64-darwin": "Darwin.arm64|Darwin.aarch64)",
        "x86_64-darwin": "Darwin.x86_64)",
        "aarch64-linux": "Linux.aarch64)",
        "x86_64-linux": "Linux.x86_64)",
    }
    for system, selector in platform_cases.items():
        expected = (
            f"{selector}\n    PLATFORM_SHA256={nix_pin['binaryTarballSha256'][system]}"
        )
        if expected not in source:
            refuse(f"Nix bootstrap lost the pinned {system} platform mapping")
    for forbidden in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "github.token",
        "github_access_token",
        "NIX_SECRET_KEY_FILE",
        "CACHIX_AUTH_TOKEN",
    ):
        if forbidden in source:
            refuse(f"Nix bootstrap references a live credential surface: {forbidden}")


def verify_nix_bootstrap(path: Path, nix_pin: dict) -> None:
    if path.is_symlink() or not path.is_file():
        refuse("the reviewed Nix bootstrap must be a repository-local regular file")
    if not path.stat().st_mode & 0o111:
        refuse("the reviewed Nix bootstrap must remain executable")
    source_bytes = path.read_bytes()
    observed = hashlib.sha256(source_bytes).hexdigest()
    if observed != nix_pin.get("wrapperSha256"):
        refuse("the repository-pinned Nix bootstrap digest changed")
    if observed != REVIEWED_NIX_BOOTSTRAP["wrapperSha256"]:
        refuse("the independently reviewed Nix bootstrap digest changed")
    verify_nix_bootstrap_source(source_bytes.decode("utf-8"), REVIEWED_NIX_BOOTSTRAP)


def verify_fixture(pins: dict) -> None:
    lock = json.loads((FIXTURE / "flake.lock").read_text(encoding="utf-8"))
    darwin_node = lock.get("nodes", {}).get("nix-darwin", {})
    darwin_lock = darwin_node.get("locked", {})
    darwin_original = darwin_node.get("original", {})
    nixpkgs_lock = lock.get("nodes", {}).get("nixpkgs", {}).get("locked", {})
    if (
        darwin_lock.get("owner") != "axiomlayer"
        or darwin_lock.get("repo") != "nix-darwin"
        or darwin_original.get("owner") != "axiomlayer"
        or darwin_original.get("repo") != "nix-darwin"
    ):
        refuse("fixture must consume the AxiomLayer true fork")
    if darwin_lock.get("rev") != pins["sourceCommit"]:
        refuse("fixture nix-darwin revision drifted")
    if darwin_lock.get("narHash") != pins["sourceArchive"]["narHash"]:
        refuse("fixture nix-darwin NAR hash drifted")
    if nixpkgs_lock.get("rev") != pins["nixpkgs"]["revision"]:
        refuse("fixture nixpkgs revision drifted")
    if nixpkgs_lock.get("narHash") != pins["nixpkgs"]["narHash"]:
        refuse("fixture nixpkgs NAR hash drifted")

    flake = (FIXTURE / "flake.nix").read_text(encoding="utf-8")
    module = (FIXTURE / "fleet-darwin.nix").read_text(encoding="utf-8")
    gate = (INTEGRATION / "run-darwin-gate.sh").read_text(encoding="utf-8")
    for system in pins["systems"]:
        if system not in flake:
            refuse(f"fixture does not expose {system}")
    if f'url = "github:{CANONICAL_REPOSITORY}/{pins["sourceCommit"]}";' not in flake:
        refuse("fixture must use the exact lowercase AxiomLayer fork source")
    for surface in (
        "nix.enable = false",
        "systemPackages",
        "homebrew =",
        "caskArgs.require_sha = true",
        "system.defaults",
        "launchd.user.agents.axiom-fleet-bootstrap",
        "launchd.daemons.axiom-fleet-runner",
        "/run/axiom-ci/fabricated-runner-token",
    ):
        if surface not in module:
            refuse(f"public fixture lost Darwin surface: {surface}")
    for forbidden in (
        "ghp" + "_",
        "github" + "_pat_",
        "begin " + "private key",
        "/nix/store/fabricated-runner-token",
    ):
        if forbidden in module.lower():
            refuse(f"fixture contains forbidden credential material: {forbidden}")
    for mutator in ("darwin-rebuild switch", "launchctl", "osascript", "brew bundle"):
        if mutator in gate.lower():
            refuse(f"host activation escaped into the hosted gate: {mutator}")


def verify_release_isolation() -> None:
    script = (ROOT / "scripts/release.sh").read_text(encoding="utf-8")
    guard = "release.sh: refusing outside the canonical nix-darwin upstream repository"
    if guard not in script or script.index(guard) > script.index("git checkout master"):
        refuse("release.sh does not fail closed before mutating Git state")


def verify_machine_identities() -> None:
    paths = (
        PINS_FILE,
        FIXTURE / "flake.nix",
        FIXTURE / "flake.lock",
        ROOT / ".github/workflows/axiomlayer-integration.yml",
    )
    for path in paths:
        verify_machine_identity_text(
            path.read_text(encoding="utf-8"), path.relative_to(ROOT)
        )


def verify_retired_surface_absent() -> None:
    retired = "_".join(("codex", "security", "gate"))
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if retired in text.lower():
            refuse(f"retired security surface reappeared in {path.relative_to(ROOT)}")


def verify_local_source(pins: dict) -> None:
    try:
        run("git", "cat-file", "-e", pins["sourceCommit"] + "^{commit}")
        raw = run("git", "show", pins["sourceCommit"] + ":version.json").stdout
    except subprocess.CalledProcessError as error:
        refuse(f"locked source commit is not present locally: {error}")
    if json.loads(raw) != pins["releaseMetadata"]:
        refuse("locked source version.json does not match the promotion authority")


def get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AxiomLayer-nix-darwin-integration",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def ls_remote(url: str, ref: str) -> str:
    result = run("git", "ls-remote", url, ref)
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or rows[0][1] != ref:
        refuse(f"{url} does not expose exactly one {ref}")
    return rows[0][0]


def download(url: str, limit: int = 5 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "AxiomLayer-nix-darwin-integration"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        refuse("source archive exceeded the reviewed size ceiling")
    return data


def verify_live_provenance(pins: dict) -> None:
    ref = "refs/heads/" + pins["releaseBranch"]
    upstream_sha = ls_remote("git@github.com:nix-darwin/nix-darwin.git", ref)
    fork_sha = ls_remote("git@github.com:axiomlayer/nix-darwin.git", ref)
    if upstream_sha != pins["sourceCommit"] or fork_sha != pins["sourceCommit"]:
        refuse(
            "the upstream and fork release branches no longer agree with the locked commit"
        )

    fork = get_json("https://api.github.com/repos/axiomlayer/nix-darwin")
    if (
        not fork.get("fork")
        or fork.get("parent", {}).get("full_name") != pins["upstream"]
    ):
        refuse("axiomlayer/nix-darwin is not a true fork of the declared upstream")

    commit = get_json(
        "https://api.github.com/repos/nix-darwin/nix-darwin/commits/"
        + pins["sourceCommit"]
    )
    if commit.get("sha") != pins["sourceCommit"]:
        refuse("GitHub returned the wrong source commit")
    if not commit.get("commit", {}).get("verification", {}).get("verified"):
        refuse("the authoritative upstream source commit is no longer verified")

    archive = download(pins["sourceArchive"]["url"])
    observed_sha = hashlib.sha256(archive).hexdigest()
    if observed_sha != pins["sourceArchive"]["sha256"]:
        refuse(f"source archive digest changed: {observed_sha}")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as stream:
        versions = [
            member
            for member in stream.getmembers()
            if member.name.endswith("/version.json")
        ]
        if len(versions) != 1:
            refuse("source archive does not contain exactly one version.json")
        extracted = stream.extractfile(versions[0])
        if extracted is None or json.load(extracted) != pins["releaseMetadata"]:
            refuse("source archive release metadata changed")


def safe_extract_git_archive(revision: str, destination: Path) -> str:
    if destination.exists():
        refuse(f"materialization destination already exists: {destination}")
    resolved_parent = destination.parent.resolve()
    if resolved_parent == Path("/"):
        refuse("refusing a broad materialization destination")
    destination.mkdir(parents=True)
    resolved_destination = destination.resolve()

    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        members = stream.getmembers()
        for member in members:
            target = (resolved_destination / member.name).resolve()
            if (
                resolved_destination not in target.parents
                and target != resolved_destination
            ):
                refuse(f"archive path escapes destination: {member.name}")
            if member.issym() or member.islnk():
                refuse(f"archive links are not accepted: {member.name}")
        stream.extractall(resolved_destination, members=members, filter="data")
    return run("git", "rev-parse", revision + "^{commit}").stdout.strip()


def materialize(pins: dict, revision_kind: str, destination: Path) -> None:
    if revision_kind == "locked":
        revision = pins["sourceCommit"]
    elif revision_kind == "candidate":
        revision = "HEAD"
    elif revision_kind == "upstream-master":
        upstream_url = "git@github.com:nix-darwin/nix-darwin.git"
        expected = ls_remote(upstream_url, "refs/heads/master")
        run(
            "git",
            "fetch",
            "--no-tags",
            "--depth=1",
            upstream_url,
            "refs/heads/master",
        )
        revision = "FETCH_HEAD"
        fetched = run("git", "rev-parse", revision + "^{commit}").stdout.strip()
        if fetched != expected:
            refuse("upstream master moved while the candidate was being materialized")
    else:
        refuse(f"unsupported materialization kind: {revision_kind}")
    resolved = safe_extract_git_archive(revision, destination)
    print(
        f"materialized_revision={resolved} kind={revision_kind} destination={destination}"
    )


def verify(live: bool) -> dict:
    pins = load_pins()
    verify_pins(pins)
    verify_nix_bootstrap(INTEGRATION / "install-nix-ci.sh", pins["nix"])
    verify_workflows(pins)
    verify_fixture(pins)
    verify_release_isolation()
    verify_machine_identities()
    verify_retired_surface_absent()
    verify_local_source(pins)
    if live:
        verify_live_provenance(pins)
    mode = "static+live" if live else "static"
    print(
        "integration_contract=verified "
        f"mode={mode} version={pins['version']} source={pins['sourceCommit']} "
        f"systems={len(pins['systems'])} consumers={len(pins['fleetConsumers'])}"
    )
    return pins


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("verify", "materialize"), nargs="?", default="verify"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--revision",
        choices=("locked", "candidate", "upstream-master"),
        default="locked",
    )
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "verify":
            verify(args.live)
        else:
            if args.destination is None:
                parser.error("materialize requires --destination")
            materialize(load_pins(), args.revision, args.destination)
    except (
        ContractError,
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"integration contract refused: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
