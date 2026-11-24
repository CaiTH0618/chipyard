#!/usr/bin/env bash
# Run standalone logic/SRAM area estimation without building a simulator.
set -eo pipefail
xdna2_area_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$xdna2_area_root"
source env.sh
source scripts/chipyard-build-resources.sh
python3 scripts/xdna2-area-45nm.py "$@"
