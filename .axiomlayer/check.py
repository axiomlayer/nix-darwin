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
    Path(".github/workflows/test.yml"),
    Path(".github/workflows/update-website.yml"),
}
UPSTREAM_ONLY_JOBS = {
    Path(".github/workflows/test.yml"): (
        "test-stable",
        "install-against-stable",
        "install-flake",
    ),
    Path(".github/workflows/update-website.yml"): ("build", "deploy"),
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
NAR_HASH = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
ACTION_REFERENCE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)


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
        "fork": "AxiomLayer/nix-darwin",
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
            "AxiomLayer/dotfiles",
            "ee9c1a27542f7918a30d9e9e33155baea2b96c8a",
            49,
            "nix/checks/evaluation.nix",
        ),
        "margay": (
            "AxiomLayer/margay",
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

    if pins.get("nix") != {
        "version": "2.35.2",
        "installUrl": "https://releases.nixos.org/nix/nix-2.35.2/install",
    }:
        refuse("the CI Nix runtime must remain exactly 2.35.2")

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
    expected_actions = {
        "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
        "actions/deploy-pages": "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e",
        "actions/upload-pages-artifact": "56afc609e74202658d3ffba0e8f6dda462b719fa",
        "cachix/install-nix-action": "13d8dd58da0234aa297dedd986986ccb8e7f3e24",
    }
    if actions != expected_actions:
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
    return {
        path.relative_to(ROOT)
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    }


def validate_action_references(text: str, path: Path, actions: dict) -> set[str]:
    seen: set[str] = set()
    for match in ACTION_REFERENCE.finditer(text):
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if reference.startswith("docker://"):
            if not re.search(r"@sha256:[0-9a-f]{64}$", reference):
                refuse(f"{path}: container action is not digest-pinned: {reference}")
            continue
        if "@" not in reference:
            refuse(f"{path}: action has no immutable revision: {reference}")
        action, revision = reference.rsplit("@", 1)
        if not GIT_SHA.fullmatch(revision):
            refuse(f"{path}: {action} is not pinned to a full commit SHA")
        if actions.get(action) != revision:
            refuse(f"{path}: unreviewed action revision {action}@{revision}")
        seen.add(action)
    return seen


def verify_upstream_job_guards(path: Path, text: str) -> None:
    expected_guard = "github.repository == 'nix-darwin/nix-darwin'"
    for job in UPSTREAM_ONLY_JOBS[path]:
        pattern = re.compile(
            rf"^  {re.escape(job)}:\n(?:    [^\n]+\n)*?    if: {re.escape(expected_guard)}$",
            re.MULTILINE,
        )
        if not pattern.search(text):
            refuse(f"{path}: upstream-only job {job!r} lacks its repository guard")


def verify_workflows(pins: dict) -> None:
    found = workflow_files()
    if found != EXPECTED_WORKFLOWS:
        refuse(f"workflow inventory changed: {sorted(map(str, found))}")

    all_seen: set[str] = set()
    checkout_count = 0
    credential_guard_count = 0
    for path in sorted(found):
        text = (ROOT / path).read_text(encoding="utf-8")
        lowered = text.lower()
        all_seen.update(validate_action_references(text, path, pins["actions"]))
        checkout_count += text.count("uses: actions/checkout@")
        credential_guard_count += text.count("persist-credentials: false")

        if "secrets." in lowered or "secrets: inherit" in lowered:
            refuse(
                f"{path}: CI must not consume real repository or organization secrets"
            )
        if "pull_request_target:" in text:
            refuse(f"{path}: privileged pull_request_target execution is forbidden")

        if path in UPSTREAM_ONLY_JOBS:
            verify_upstream_job_guards(path, text)

    if all_seen != set(pins["actions"]):
        refuse("workflow action use and the reviewed Action allowlist disagree")
    if checkout_count != credential_guard_count:
        refuse("every checkout must disable persisted credentials")

    integration = (ROOT / ".github/workflows/axiomlayer-integration.yml").read_text(
        encoding="utf-8"
    )
    if not re.search(r"^permissions:\n  contents: read$", integration, re.MULTILINE):
        refuse("AxiomLayer integration permissions must be read-only contents")
    for fragment in (
        "id-token: write",
        "contents: write",
        "packages: write",
        "pages: write",
        "pull-requests: write",
        "environment:",
        "gh release",
        "git push",
        "nix copy",
        "cachix push",
        "darwin-rebuild switch",
        "launchctl",
        "osascript",
    ):
        if fragment in integration.lower():
            refuse(f"AxiomLayer integration contains a mutating capability: {fragment}")
    if integration.count("github.repository == 'AxiomLayer/nix-darwin'") < 3:
        refuse("every AxiomLayer integration job must be fork-scoped")
    for fabricated in (
        "fabricated-nix-darwin-enrollment-token",
        "fabricated-nix-darwin-passphrase",
        "fabricated-nix-darwin-encryption-key",
    ):
        if fabricated not in integration:
            refuse("secret-shaped integration inputs must remain explicit fabrications")

    website = (ROOT / ".github/workflows/update-website.yml").read_text(
        encoding="utf-8"
    )
    if "pages: write" not in website or "id-token: write" not in website:
        refuse("upstream Pages permissions changed and require review")
    if website.count("github.repository == 'nix-darwin/nix-darwin'") != 2:
        refuse("both upstream Pages jobs must be inert in AxiomLayer")


def verify_fixture(pins: dict) -> None:
    lock = json.loads((FIXTURE / "flake.lock").read_text(encoding="utf-8"))
    darwin_lock = lock.get("nodes", {}).get("nix-darwin", {}).get("locked", {})
    nixpkgs_lock = lock.get("nodes", {}).get("nixpkgs", {}).get("locked", {})
    if (
        darwin_lock.get("owner") != "AxiomLayer"
        or darwin_lock.get("repo") != "nix-darwin"
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
    upstream_sha = ls_remote("https://github.com/nix-darwin/nix-darwin.git", ref)
    fork_sha = ls_remote("https://github.com/AxiomLayer/nix-darwin.git", ref)
    if upstream_sha != pins["sourceCommit"] or fork_sha != pins["sourceCommit"]:
        refuse(
            "the upstream and fork release branches no longer agree with the locked commit"
        )

    fork = get_json("https://api.github.com/repos/AxiomLayer/nix-darwin")
    if (
        not fork.get("fork")
        or fork.get("parent", {}).get("full_name") != pins["upstream"]
    ):
        refuse("AxiomLayer/nix-darwin is not a true fork of the declared upstream")

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
        upstream_url = "https://github.com/nix-darwin/nix-darwin.git"
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
    verify_workflows(pins)
    verify_fixture(pins)
    verify_release_isolation()
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
