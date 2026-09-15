# AxiomLayer nix-darwin integration

This directory is the read-only integration boundary between the true
`AxiomLayer/nix-darwin` fork and the fleet. The authoritative snapshot comes
from dotfiles pull request 49:

- release branch: `nix-darwin-26.05`
- source commit: `c3e90c89649b07d1a96e4b9dd6cd0d6e44b91a74`
- source archive SHA-256: `1f73979fb4ecbfb1fb4a3869fc8386c5d437f0f5f5850e655e9a9777aaf284ce`
- source NAR hash: `sha256-2cp6N3rrwnGYLTx9l6N+NI+kwrCWxvJUbj5WJhvB29A=`

`nix-darwin-26.05` is an upstream release branch, not a tag. The verifier
requires both upstream and fork branch refs to resolve to the exact source
commit and checks the commit's verified GitHub signature and archive digest.

## What the gate proves

The public fixture exercises the fleet-facing nix-darwin surfaces without
exposing private repositories: external ownership of the Nix runtime,
declarative packages, Homebrew casks with checksum enforcement, GUI defaults,
a temporary launchd bootstrap supervisor, a permanent launchd self-runner
adapter, post-activation composition, and both Darwin architectures.

Every pull request evaluates the exact locked source and the pull request
candidate. A daily hosted Linux canary evaluates both Darwin architectures
against current upstream `master`; a weekly hosted Apple silicon and Intel
pair builds the native closures. A manual dispatch also tests current upstream
`master`. The gate never activates a configuration.

The two private consumer revisions are provenance labels only. CI never checks
out a private repository, receives a cross-repository token, or inherits an
organization secret. Secret-shaped inputs are explicit fabricated literals.
No cache signing, publishing, deployment, enrollment, passphrase, or
encryption authority exists in this workflow.

## Fork isolation

The inherited switch/uninstall test jobs and Pages jobs are guarded so they
only execute in `nix-darwin/nix-darwin`. The release script refuses before its
first Git mutation unless `origin` is the canonical upstream repository. All
third-party Actions use reviewed full commit SHAs, checkout credentials are
discarded, and the AxiomLayer workflow has read-only contents permission.

Run the local static and provenance proof with:

```console
python3 .axiomlayer/check.py verify --live
python3 -m unittest discover -s .axiomlayer -p 'test_*.py' -v
bash -n .axiomlayer/run-darwin-gate.sh scripts/release.sh
```

The Nix evaluation/build commands require the fleet-pinned Nix 2.35.2. They
run in CI when Nix is not yet present on the authoring host.

## Deliberately host-only

Hosted CI cannot accept a real enrollment token, enroll Margay's runner,
activate Margay, prove a cold-boot resume, render GUI applications, or verify
Homebrew and App Store convergence on the physical machine. Those remain
explicit Margay acceptance gates; passing this integration workflow is not a
substitute for them.
