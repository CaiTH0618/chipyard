#!/usr/bin/env bash
# Generate standalone XDNA 2 area blocks; this never builds/runs a simulator.
set -eo pipefail

xdna2_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$xdna2_root"
source env.sh
source scripts/chipyard-build-resources.sh
set -u

if (( $# == 0 )); then
    set -- aie mem switch
fi

for xdna2_target in "$@"; do
    case "$xdna2_target" in
        aie) xdna2_config=Xdna2AIETileConfig; xdna2_top=AIETile ;;
        mem) xdna2_config=Xdna2MemTileConfig; xdna2_top=MemTile ;;
        mem-desc) xdna2_config=Xdna2DeSCMemTileConfig; xdna2_top=MemTile ;;
        switch) xdna2_config=Xdna2InterconnectSwitchConfig; xdna2_top=InterconnectSwitch ;;
        switch-edge) xdna2_config=Xdna2EdgeSwitchConfig; xdna2_top=InterconnectSwitch ;;
        switch-corner) xdna2_config=Xdna2CornerSwitchConfig; xdna2_top=InterconnectSwitch ;;
        switch-mem) xdna2_config=Xdna2MemSwitchConfig; xdna2_top=InterconnectSwitch ;;
        *) echo "Usage: $0 [aie|mem|mem-desc|switch|switch-edge|switch-corner|switch-mem ...]" >&2; exit 2 ;;
    esac
    xdna2_build="$xdna2_root/sims/verilator/generated-src/chipyard.xdna2.Xdna2AreaHarness.$xdna2_config"
    mkdir -p "$xdna2_build"
    make -C sims/verilator verilog \
        MODEL_PACKAGE=chipyard.xdna2 MODEL=Xdna2AreaHarness \
        CONFIG="$xdna2_config" TOP="$xdna2_top" ENABLE_YOSYS_FLOW=1 \
        2>&1 | tee "$xdna2_build/verilog-generation.log"
    python3 scripts/prepare-xdna2-area-rtl.py "$xdna2_build" "$xdna2_top"
done
