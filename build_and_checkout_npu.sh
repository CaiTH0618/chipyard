#!/usr/bin/env bash
set -euo pipefail

print() { printf "%s\n" "$*"; }
err() { printf "ERROR: %s\n" "$*" >&2; }

show_help() {
  cat <<'EOF'
Usage: build_and_checkout_npu.sh

This script updates remotes for several sub-repositories and attempts to pull
their latest changes, then checks out the `npu/dev` branch at the repo root.

Required environment variables:
  FIRESIM_REMOTE_URL
  GEMMINI_REMOTE_URL
  GEMMINI_ROCC_TESTS_REMOTE_URL
  ROCKET_CHIP_REMOTE_URL

Run from anywhere inside the chipyard repository; the script will operate from
the repository root.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  show_help
  exit 0
fi

# Check prerequisites
if ! command -v git >/dev/null 2>&1; then
  err "git is required but not found in PATH"
  exit 2
fi

repo_root=""
if repo_root=$(git rev-parse --show-toplevel 2>/dev/null); then
  cd "$repo_root"
else
  err "Not inside a git repository. Please run this from within chipyard.";
  exit 2
fi

required_vars=(FIRESIM_REMOTE_URL GEMMINI_REMOTE_URL GEMMINI_ROCC_TESTS_REMOTE_URL ROCKET_CHIP_REMOTE_URL)
missing=()
for v in "${required_vars[@]}"; do
  if [ -z "${!v:-}" ]; then
    missing+=("$v")
  fi
done
if [ ${#missing[@]} -gt 0 ]; then
  err "Missing required environment variables: ${missing[*]}"
  err "Please set them before running this script (example: export FIRESIM_REMOTE_URL=... )"
  exit 3
fi

set_remote() {
  local dir="$1" name="$2" url="$3"

  if git -C "$dir" remote get-url origin >/dev/null 2>&1; then
    print "Setting origin for $dir -> $url"
    git -C "$dir" remote set-url origin "$url"
  else
    print "Adding origin for $dir -> $url"
    git -C "$dir" remote add origin "$url"
  fi
}

ensure_upstream() {
  local dir="$1" upstream_url="$2"
  if git -C "$dir" remote get-url upstream >/dev/null 2>&1; then
    print "Upstream already configured in $dir"
  else
    print "Adding upstream for $dir -> $upstream_url"
    git -C "$dir" remote add upstream "$upstream_url"
  fi
}

safe_fetch() {
  local dir="$1"
  print "Fetching in $dir (no pull)"
  git -C "$dir" fetch --all --prune
}

# Try to checkout npu/dev inside a sub-repository directory
checkout_npu_dev_in_dir() {
  local dir="$1"
  print "Attempting to checkout npu/dev in $dir"
  git -C "$dir" fetch --all --prune
  if git -C "$dir" show-ref --verify --quiet refs/heads/npu/dev; then
    git -C "$dir" checkout npu/dev
    print "Checked out existing local branch npu/dev in $dir"
  else
    if git -C "$dir" ls-remote --exit-code origin refs/heads/npu/dev >/dev/null 2>&1; then
      git -C "$dir" checkout -b npu/dev origin/npu/dev
      print "Created and checked out npu/dev from origin/npu/dev in $dir"
    elif git -C "$dir" ls-remote --exit-code upstream refs/heads/npu/dev >/dev/null 2>&1; then
      git -C "$dir" checkout -b npu/dev upstream/npu/dev
      print "Created and checked out npu/dev from upstream/npu/dev in $dir"
    else
      print "Branch npu/dev not found on origin or upstream in $dir; skipping"
    fi
  fi
}

# Top-level upstream for chipyard itself
if git remote get-url upstream >/dev/null 2>&1; then
  print "Top-level upstream already exists"
else
  print "Adding top-level upstream https://github.com/CaiTH0618/chipyard"
  git remote add upstream https://github.com/CaiTH0618/chipyard
fi

print "Processing sims/firesim"
set_remote "sims/firesim" origin "$FIRESIM_REMOTE_URL"
ensure_upstream "sims/firesim" "https://github.com/CaiTH0618/firesim"
safe_fetch "sims/firesim"
checkout_npu_dev_in_dir "sims/firesim"

print "Processing generators/gemmini"
set_remote "generators/gemmini" origin "$GEMMINI_REMOTE_URL"
ensure_upstream "generators/gemmini" "https://github.com/CaiTH0618/gemmini"
safe_fetch "generators/gemmini"
checkout_npu_dev_in_dir "generators/gemmini"

print "Processing generators/gemmini/software/gemmini-rocc-tests"
set_remote "generators/gemmini/software/gemmini-rocc-tests" origin "$GEMMINI_ROCC_TESTS_REMOTE_URL"
ensure_upstream "generators/gemmini/software/gemmini-rocc-tests" "https://github.com/CaiTH0618/gemmini-rocc-tests"
safe_fetch "generators/gemmini/software/gemmini-rocc-tests"
checkout_npu_dev_in_dir "generators/gemmini/software/gemmini-rocc-tests"

print "Processing generators/rocket-chip (relative path: generators/rocket-chip)"
set_remote "generators/rocket-chip" origin "$ROCKET_CHIP_REMOTE_URL"
ensure_upstream "generators/rocket-chip" "https://github.com/CaiTH0618/rocket-chip"
safe_fetch "generators/rocket-chip"
checkout_npu_dev_in_dir "generators/rocket-chip"

print "Done. Verify changes and run any additional commands as needed."
