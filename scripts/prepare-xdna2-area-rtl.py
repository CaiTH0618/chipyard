#!/usr/bin/env python3
"""Package DUT-only RTL, SRAM blackboxes and resource counts after Chipyard generation."""

import argparse
import collections
import json
from pathlib import Path
import re


def interconnect_profile(build: Path, top: str) -> dict:
    profiles = list(build.glob("*.xdna2-profile.json"))
    if len(profiles) != 1:
        raise ValueError("Missing XDNA 2 interconnect profile; regenerate the RTL")
    profile = json.loads(profiles[0].read_text())
    expected = {"data_bits": 512, "beat_bytes": 64, "payload_bits": 577,
                "virtual_channels": 5, "buffer_depth": 2, "local_scratchpad_bits": 256,
                "mem_dma_in_flight": 64, "mem_dma_buffer_bytes": 4096,
                "mem_dma_local_ports": 2, "mem_dma_local_in_flight": 16}
    if any(profile.get(k) != v for k, v in expected.items()):
        raise ValueError(f"Unexpected interconnect profile: {profile}")
    if top == "MemTile":
        depth = profile.get("desc_queue_depth", 0)
        expected_depth = 8 if build.name.endswith(".Xdna2DeSCMemTileConfig") else 0
        if depth != expected_depth:
            raise ValueError(f"Expected DeSC queue depth {expected_depth}, got {depth}")
    source = build / "gen-collateral" / f"{top}.sv"
    header = re.search(rf"\bmodule\s+{top}\s*\((.*?)\);", source.read_text(), re.S)
    if not header:
        raise ValueError(f"Missing top-level interface: {source}")
    ports = {}
    for line in header[1].splitlines():
        match = re.match(r"\s*(input|output)\s+(?:\[(\d+):0\]\s+)?(\w+)", line)
        if match:
            _, high, name = match.groups()
            ports[name] = int(high) + 1 if high else 1
    prefixes = {"AIETile": ["io_dma_"], "MemTile": ["io_dma_", "io_scratchpad_"],
                "InterconnectSwitch": ["io_masters_", "io_slaves_"]}[top]
    profile["interface_data_bits"] = {}
    for prefix in prefixes:
        widths = {width for name, width in ports.items()
                  if name.startswith(prefix) and name.endswith("_bits_data")}
        if not widths and top == "InterconnectSwitch":
            continue  # Some positions have no local slave/master interface.
        if widths != {profile["data_bits"]}:
            raise ValueError(f"{top} {prefix} has data widths {widths}, expected 512")
        profile["interface_data_bits"][prefix] = next(iter(widths))
    if top == "MemTile":
        desc_ports = {name: width for name, width in ports.items() if name.startswith("io_desc_")}
        if bool(desc_ports) != bool(depth):
            raise ValueError("MemTile DeSC sideband does not match the configuration")
        if depth and (desc_ports.get("io_desc_configure_bits_decoderWeight") != 4 or
                      desc_ports.get("io_desc_completedBytes_1") != 64):
            raise ValueError("MemTile does not expose DeSC weights and completion feedback")
    if top == "InterconnectSwitch":
        links = {}
        for name, width in ports.items():
            match = re.fullmatch(r"io_(in|out)_(\d+)_(.*)", name)
            if match:
                direction, index, field = match.groups()
                links.setdefault(f"{direction}_{index}", {})[field] = width
        if not links:
            raise ValueError("No mesh-link ports found")
        for name, fields in links.items():
            if (fields.get("flit_0_bits_payload") != 577 or fields.get("credit_return") != 5
                    or fields.get("vc_free") != 5):
                raise ValueError(f"Wrong mesh-link widths for {name}: {fields}")
        forwards = {sum(w for f, w in fs.items() if f.startswith("flit_")) for fs in links.values()}
        if forwards != {608}:
            raise ValueError(f"Unexpected full forward interface width: {forwards}")
        profile.update(forward_interface_bits=608, reverse_control_bits=10,
                       incoming_links=sum(k.startswith("in_") for k in links),
                       outgoing_links=sum(k.startswith("out_") for k in links))
    return profile


def prepare(build: Path, top: str) -> None:
    profile = interconnect_profile(build, top)
    top_lists = list(build.glob("*.top.f"))
    if len(top_lists) != 1:
        raise ValueError(f"Expected one DUT file list in {build}, found {len(top_lists)}")
    prefix = top_lists[0].name.removesuffix(".top.f")
    sources = [Path(line) for line in top_lists[0].read_text().splitlines() if line.strip()]
    bb_list = build / f"{prefix}.bb.f"
    if bb_list.exists():
        sources += [Path(line) for line in bb_list.read_text().splitlines() if line.strip()]
    sources = list(dict.fromkeys(sources))
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)

    macros = {}
    for line in (build / f"{prefix}.top.mems.conf").read_text().splitlines():
        words = line.split()
        if words:
            macros[words[1]] = {
                "depth": int(words[words.index("depth") + 1]),
                "width_bits": int(words[words.index("width") + 1]),
            }

    counts = collections.Counter()
    macro_counts = collections.Counter()
    hierarchy = json.loads((build / "top_module_hierarchy.json").read_text())
    if hierarchy["module_name"] != top:
        raise ValueError(f"Expected {top}, found {hierarchy['module_name']}")

    def visit(node):
        name = node["module_name"]
        counts[re.sub(r"_\d+$", "", name)] += 1
        if name in macros:
            macro_counts[name] += 1
        for child in node["instances"]:
            visit(child)

    visit(hierarchy)
    sram_bytes = sum(
        macros[name]["depth"] * macros[name]["width_bits"] // 8 * count
        for name, count in macro_counts.items()
    )
    expected = {
        "AIETile": {"Gemmini": 1, "MacUnit": 1024, "FP32Simd": 1, "FP32SimdLane": 32,
                    "FP32SimdEngine": 1, "FP32VectorRegisterFile": 1,
                    "AIETileSharedMemory": 1, "AIETileSramBank": 8,
                    "MulAddRecFNPipe_l2_e8_s24": 32, "ActiveSPM": 0, "Router": 0},
        "MemTile": {"Gemmini": 0, "MacUnit": 0, "ActiveSPM": 1, "ActiveSPMDMA": 1,
                    "ActiveSPMConcurrentDMA": 1, "ActiveSPMByteDMA": 1,
                    "ActiveSPMOrderedAccess": 1, "TLRAM": 8, "Router": 0},
        "InterconnectSwitch": {"Gemmini": 0, "MacUnit": 0, "ActiveSPM": 0, "Router": 2},
    }[top]
    if top == "MemTile":
        desc_depth = profile.get("desc_queue_depth", 0)
        expected["ActiveSPMDeSCAdmission"] = int(desc_depth > 0)
        if desc_depth:
            expected[f"Queue{desc_depth}_ActiveSPMDeSCBurst"] = 2
    expected.update(RocketTile=0, ReRoCCManagerTile=0, TLMonitor=0)
    for name, count in expected.items():
        if counts[name] != count:
            raise ValueError(f"{top}: expected {count} {name}, found {counts[name]}")
    expected_sram = {"AIETile": 128 * 1024, "MemTile": 512 * 1024}.get(top)
    if expected_sram is not None and sram_bytes != expected_sram:
        raise ValueError(f"{top}: expected {expected_sram} SRAM bytes, found {sram_bytes}")
    if top == "MemTile":
        dma = (build / "gen-collateral/ActiveSPMConcurrentDMA.sv").read_text()
        data_regs = re.findall(r"\breg\s+\[255:0\]\s+data_[01]_\d+\s*;", dma)
        if len(data_regs) != 128:
            raise ValueError(f"Expected 4096 bytes of DMA flop data, found {len(data_regs) * 32}")
        for lane in range(2):
            if not re.search(rf"output\s+\[255:0\]\s+io_local_{lane}_a_bits_data", dma):
                raise ValueError(f"Missing 256-bit private DMA lane {lane}")
        if not re.search(r"output\s+\[5:0\]\s+io_external_a_bits_source", dma):
            raise ValueError("DMA does not expose 64 external source IDs")

    memory_model = build / "gen-collateral" / f"{prefix}.top.mems.v"
    # The generated macro model uses ANSI port declarations. Keep only each
    # macro's interface; its behavioral/flop implementation is excluded here.
    headers = {name: header for header, name in re.findall(
        r"(module\s+(\w+)\s*\(.*?\);)", memory_model.read_text(), re.S
    )}
    missing_macros = set(macros) - headers.keys()
    if missing_macros:
        raise ValueError(f"Missing SRAM interfaces: {sorted(missing_macros)}")
    blackboxes = build / "srams.blackboxes.v"
    blackboxes.write_text("\n".join(
        f"(* blackbox *)\n{headers[name]}\nendmodule\n" for name in macros
    ))
    (build / "rtl.f").write_text("".join(f"{path}\n" for path in sources + [memory_model]))
    (build / "synthesis.f").write_text("".join(f"{path}\n" for path in sources + [blackboxes]))
    summary = {
        "top": top,
        "interconnect": profile,
        "macs_per_cycle": counts["MacUnit"],
        "int8_macs_per_cycle": counts["MacUnit"],
        "fp32_simd_lanes": counts["FP32SimdLane"],
        "fp32_fmas_per_cycle": counts["FP32SimdLane"],
        "fp32_flops_per_cycle": 2 * counts["FP32SimdLane"],
        "fp32_latency_cycles": 3 if counts["FP32SimdLane"] else 0,
        "simd_scratchpad_connected": counts["AIETileSharedMemory"] == 1,
        "fp32_vector_registers": 8 if counts["FP32VectorRegisterFile"] else 0,
        "fp32_vector_register_bytes": 1024 if counts["FP32VectorRegisterFile"] else 0,
        "sram_bytes": sram_bytes,
        "resource_counts": {name: counts[name] for name in expected},
        "sram_macros": [dict(name=name, instances=macro_counts[name], **shape) for name, shape in macros.items()],
        "rtl_source_files": len(sources) + 1,
        "rtl_source_bytes": sum(path.stat().st_size for path in sources + [memory_model]),
    }
    (build / "resources.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{top}: {counts['MacUnit']} INT8 MACs/cycle, {counts['FP32SimdLane']} FP32 FMA lanes, "
          f"{sram_bytes} SRAM bytes; packaged RTL in {build}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("top", choices=["AIETile", "MemTile", "InterconnectSwitch"])
    args = parser.parse_args()
    prepare(args.build_dir.resolve(), args.top)
