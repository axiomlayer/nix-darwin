# AxiomLayer nix-darwin integration

This directory is the read-only integration boundary between the true
`axiomlayer/nix-darwin` fork and the fleet. The authoritative snapshot comes
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
against current upstream `master`; the same daily run uses a hosted Apple
silicon and Intel pair to build the native closures. A manual dispatch also
tests current upstream `master`. The gate never activates a configuration.

The two private consumer revisions are provenance labels only. CI never checks
out a private repository, receives a cross-repository token, or inherits an
organization secret. Secret-shaped inputs are explicit fabricated literals.
No cache signing, publishing, release, synchronization, deployment,
environment, enrollment, passphrase, or encryption authority exists in this
workflow.

## Fork isolation

The inherited switch/uninstall and Pages workflows are preserved as `.disabled`
snapshots under `.github/upstream-workflows/`, outside GitHub's executable
workflow directory. Their upstream-owner guards remain as defense in depth.
The release script refuses before its first Git mutation unless `origin` is the
canonical upstream repository. All third-party Actions use reviewed full commit
SHAs, and every checkout requires a clean tree and discards credentials. The
AxiomLayer workflow has read-only contents permission and uses only dated
GitHub-hosted runner labels.

Every executable job binds to the exact lowercase
`axiomlayer/nix-darwin` identity. Pull requests are accepted only for `master`
merge refs; pushes require protected `master`; scheduled and manual runs also
require protected `master` and the exact default-branch identity of
`.github/workflows/axiomlayer-integration.yml`.

Hosted jobs install Nix through `.axiomlayer/install-nix-ci.sh`. The wrapper
pins Nix 2.35.2, verifies the official launcher SHA-256, selects and requires
the matching embedded tarball digest for all four supported Darwin/Linux
architectures, and invokes the reviewed launcher under `env -i`. Its exact
bytes are independently pinned in `pins.json` and `check.py`.

Run the local static and provenance proof with:

```console
python3 .axiomlayer/check.py verify --live
python3 -m unittest discover -s .axiomlayer -p 'test_*.py' -v
bash -n .axiomlayer/install-nix-ci.sh .axiomlayer/run-darwin-gate.sh scripts/release.sh
```

The Nix evaluation/build commands require the fleet-pinned Nix 2.35.2. They
run in CI when Nix is not yet present on the authoring host.

## Deliberately host-only

Hosted CI cannot accept a real enrollment token, enroll Margay's runner,
activate Margay, prove a cold-boot resume, render GUI applications, or verify
Homebrew and App Store convergence on the physical machine. Those remain
explicit Margay acceptance gates; passing this integration workflow is not a
substitute for them.
