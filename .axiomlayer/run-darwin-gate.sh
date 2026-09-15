#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "usage: run-darwin-gate.sh evaluate CANDIDATE_KIND | native CANDIDATE_KIND SYSTEM" >&2
  exit 2
}

mode=${1:-}
candidate_kind=${2:-}
native_system=${3:-}

case "$mode" in
  evaluate) [ "$#" -eq 2 ] || usage ;;
  native) [ "$#" -eq 3 ] || usage ;;
  *) usage ;;
esac

case "$candidate_kind" in
  candidate | upstream-master) ;;
  *) usage ;;
esac

if [ "$mode" = native ]; then
  case "$native_system" in
    aarch64-darwin | x86_64-darwin) ;;
    *) usage ;;
  esac
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
fixture="$root/.axiomlayer/fleet-fixture"
scratch_parent=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
scratch=$(mktemp -d "$scratch_parent/axiom-nix-darwin-gate.XXXXXX")
locked="$scratch/locked"
candidate="$scratch/candidate"

python3 "$root/.axiomlayer/check.py" materialize \
  --revision locked \
  --destination "$locked"
python3 "$root/.axiomlayer/check.py" materialize \
  --revision "$candidate_kind" \
  --destination "$candidate"

test "${FLEET_TEST_RUNNER_TOKEN:-}" = fabricated-nix-darwin-enrollment-token
test "${FLEET_TEST_PASSPHRASE:-}" = fabricated-nix-darwin-passphrase
test "${FLEET_TEST_ENCRYPTION_KEY:-}" = fabricated-nix-darwin-encryption-key

observed_nix=$(nix --version)
test "$observed_nix" = "nix (Nix) 2.35.2"

nix_command() {
  nix \
    --extra-experimental-features "nix-command flakes" \
    --option accept-flake-config false \
    --option flake-registry "" \
    "$@"
}

evaluate_source() {
  source_name=$1
  source_path=$2

  nix_command flake metadata \
    --no-write-lock-file \
    "$source_path" >/dev/null
  nix_command flake check \
    --all-systems \
    --no-build \
    --no-write-lock-file \
    "$source_path"

  for configuration in fleet-arm64 fleet-intel; do
    drv_path=$(
      nix_command eval \
        --raw \
        --no-write-lock-file \
        "$fixture#darwinConfigurations.$configuration.config.system.build.toplevel.drvPath" \
        --override-input nix-darwin "path:$source_path"
    )
    case "$drv_path" in
      /nix/store/*.drv) ;;
      *)
        echo "unexpected Darwin derivation path for $source_name/$configuration: $drv_path" >&2
        exit 1
        ;;
    esac
  done

  echo "darwin_evaluation=verified source=$source_name"
}

build_native_source() {
  source_name=$1
  source_path=$2

  contract_path=$(
    nix_command build \
      --no-link \
      --print-out-paths \
      --no-write-lock-file \
      "$fixture#checks.$native_system.fleet-contract" \
      --override-input nix-darwin "path:$source_path"
  )
  toplevel_path=$(
    nix_command build \
      --no-link \
      --print-out-paths \
      --no-write-lock-file \
      "$fixture#checks.$native_system.fleet-darwin" \
      --override-input nix-darwin "path:$source_path"
  )

  test -f "$contract_path"
  test -e "$toplevel_path/activate"
  python3 - "$contract_path" "$native_system" <<'PY'
import json
import sys

path, expected_system = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    contract = json.load(stream)

expected = {
    "caskRequireSha": True,
    "computerName": "AxiomLayer Darwin CI",
    "nixManagedByDarwin": False,
    "primaryUser": "fleet-ci",
    "runnerTokenPath": "/run/axiom-ci/fabricated-runner-token",
    "stateVersion": 7,
    "system": expected_system,
    "temporarySupervisor": "com.axiomlayer.fleet-bootstrap",
}
for key, value in expected.items():
    if contract.get(key) != value:
        raise SystemExit(f"contract mismatch for {key}: {contract.get(key)!r}")
if not isinstance(contract.get("darwinRelease"), str):
    raise SystemExit("darwinRelease is missing")
PY

  echo "darwin_native_build=verified source=$source_name system=$native_system"
}

evaluate_source locked "$locked"
evaluate_source candidate "$candidate"

if [ "$mode" = native ]; then
  build_native_source locked "$locked"
  build_native_source candidate "$candidate"
fi

echo "darwin_gate=verified mode=$mode candidate_kind=$candidate_kind scratch=$scratch"
