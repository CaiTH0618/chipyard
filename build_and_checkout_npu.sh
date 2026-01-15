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
  if [ ! -d "$dir" ] || [ ! -d "$dir/.git" ]; then
    return 0
  fi
  if git -C "$dir" remote get-url upstream >/dev/null 2>&1; then
    print "Upstream already configured in $dir"
  else
    print "Adding upstream for $dir -> $upstream_url"
    git -C "$dir" remote add upstream "$upstream_url" || true
  fi
}

safe_pull() {
  local dir="$1"
  if [ ! -d "$dir" ] || [ ! -d "$dir/.git" ]; then
    return 0
  fi
  print "Fetching and pulling in $dir"
  git -C "$dir" fetch --all --prune || true
  # Try fast-forward first; fall back to regular pull if necessary
  if ! git -C "$dir" pull --ff-only 2>/dev/null; then
    git -C "$dir" pull --no-rebase || true
  fi
}

# Top-level upstream for chipyard itself
if git remote get-url upstream >/dev/null 2>&1; then
  print "Top-level upstream already exists"
else
  print "Adding top-level upstream https://github.com/CaiTH0618/chipyard"
  git remote add upstream https://github.com/CaiTH0618/chipyard || true
fi

print "Processing sims/firesim"
set_remote "sims/firesim" origin "$FIRESIM_REMOTE_URL"
ensure_upstream "sims/firesim" "https://github.com/CaiTH0618/firesim"
safe_pull "sims/firesim"

print "Processing generators/gemmini"
set_remote "generators/gemmini" origin "$GEMMINI_REMOTE_URL"
ensure_upstream "generators/gemmini" "https://github.com/CaiTH0618/gemmini"
safe_pull "generators/gemmini"

print "Processing generators/gemmini/software/gemmini-rocc-tests"
set_remote "generators/gemmini/software/gemmini-rocc-tests" origin "$GEMMINI_ROCC_TESTS_REMOTE_URL"
ensure_upstream "generators/gemmini/software/gemmini-rocc-tests" "https://github.com/CaiTH0618/gemmini-rocc-tests"
safe_pull "generators/gemmini/software/gemmini-rocc-tests"

print "Processing generators/rocket-chip (relative path: generators/rocket-chip)"
set_remote "generators/rocket-chip" origin "$ROCKET_CHIP_REMOTE_URL"
ensure_upstream "generators/rocket-chip" "https://github.com/CaiTH0618/rocket-chip"
safe_pull "generators/rocket-chip"

# Final: attempt to checkout npu/dev at repo root
print "Attempting to checkout branch npu/dev at repository root"
git fetch --all --prune || true
if git show-ref --verify --quiet refs/heads/npu/dev; then
  git checkout npu/dev
  print "Checked out existing local branch npu/dev"
else
  if git ls-remote --exit-code origin refs/heads/npu/dev >/dev/null 2>&1; then
    git checkout -b npu/dev origin/npu/dev
    print "Created and checked out npu/dev from origin/npu/dev"
  elif git ls-remote --exit-code upstream refs/heads/npu/dev >/dev/null 2>&1; then
    git checkout -b npu/dev upstream/npu/dev
    print "Created and checked out npu/dev from upstream/npu/dev"
  else
    err "Branch npu/dev not found on origin or upstream. You may need to create it or fetch from another remote."
    exit 4
  fi
fi

print "Cleaning up backup files if present"

print "Done. Verify changes and run any additional commands as needed."
