#!/usr/bin/env python3
"""Estimate standalone XDNA 2 logic with Nangate45/Yosys and SRAM with CACTI."""

import argparse
import collections
import csv
import fcntl
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ORFS_REV = "23341145f0fc92a522de26d5244a4a0013818f97"
LIB_NAME = "NangateOpenCellLibrary_typical.lib"
LIB_SHA256 = "8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1"
ORFS_BASE = f"https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/{ORFS_REV}/flow/platforms/nangate45"
BLOCKS = {
    "aie": ("Xdna2AIETileConfig", "AIETile"),
    "mem": ("Xdna2MemTileConfig", "MemTile"),
    "mem-desc": ("Xdna2DeSCMemTileConfig", "MemTile"),
    "switch": ("Xdna2InterconnectSwitchConfig", "InterconnectSwitch"),
    "switch-edge": ("Xdna2EdgeSwitchConfig", "InterconnectSwitch"),
    "switch-corner": ("Xdna2CornerSwitchConfig", "InterconnectSwitch"),
    "switch-mem": ("Xdna2MemSwitchConfig", "InterconnectSwitch"),
}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run(command, cwd, log, timeout):
    print("Running:", " ".join(map(str, command)), flush=True)
    start = time.monotonic()
    with Path(log).open("w") as output:
        process = subprocess.run(list(map(str, command)), cwd=cwd, stdout=output,
                                 stderr=subprocess.STDOUT, timeout=timeout)
    if process.returncode:
        tail = "\n".join(Path(log).read_text(errors="replace").splitlines()[-15:])
        raise RuntimeError(f"Command failed ({process.returncode}); see {log}\n{tail}")
    return round(time.monotonic() - start, 3)


def quoted(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"') + '"'


def liberty(args):
    if args.liberty:
        path = args.liberty.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    directory = args.output / "tech"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LIB_NAME
    if not path.exists():
        print(f"Downloading pinned Nangate45 library ({ORFS_REV})", flush=True)
        temporary = path.with_suffix(".download")
        with urllib.request.urlopen(f"{ORFS_BASE}/lib/{LIB_NAME}", timeout=90) as source:
            temporary.write_bytes(source.read())
        if digest(temporary) != LIB_SHA256:
            raise ValueError("Downloaded Nangate45 Liberty checksum mismatch")
        temporary.replace(path)
    if digest(path) != LIB_SHA256:
        raise ValueError(f"Nangate45 library checksum mismatch: {path}")
    license_file = directory / "upstream-platform-LICENSE"
    if not license_file.exists():
        with urllib.request.urlopen(f"{ORFS_BASE}/LICENSE", timeout=30) as source:
            license_file.write_bytes(source.read())
    write_json(directory / "source.json", {
        "repository": "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts",
        "revision": ORFS_REV, "url": f"{ORFS_BASE}/lib/{LIB_NAME}", "sha256": LIB_SHA256,
        "corner": "typical, 1.1 V, 25 C", "cell_area_unit": "um^2",
    })
    return path


def macro_shapes(build):
    files = list(build.glob("*.top.mems.conf"))
    if len(files) != 1:
        raise ValueError(f"Expected one top.mems.conf in {build}")
    shapes = {}
    for line in files[0].read_text().splitlines():
        words = line.split()
        if not words:
            continue
        ports = words[words.index("ports") + 1].split(",")
        rw = sum(p in ("rw", "mrw") for p in ports)
        read = sum(p in ("r", "mr") for p in ports)
        write = sum(p in ("w", "mw") for p in ports)
        if rw + read + write != len(ports):
            raise ValueError(f"Unsupported memory ports: {ports}")
        shapes[words[1]] = {
            "depth": int(words[words.index("depth") + 1]),
            "width_bits": int(words[words.index("width") + 1]),
            "rw_ports": rw, "read_ports": read, "write_ports": write,
            "mask_granularity_bits": int(words[words.index("mask_gran") + 1]) if "mask_gran" in words else None,
        }
    resources = json.loads((build / "resources.json").read_text())
    for macro in resources["sram_macros"]:
        shape = shapes[macro["name"]]
        if (macro["depth"], macro["width_bits"]) != (shape["depth"], shape["width_bits"]):
            raise ValueError("SRAM metadata disagrees with the generated memory configuration")
        shape["instances"] = macro["instances"]
    if any("instances" not in shape for shape in shapes.values()):
        raise ValueError("Missing SRAM instance multiplicities")
    actual_bytes = sum(s["depth"] * s["width_bits"] // 8 * s["instances"] for s in shapes.values())
    if actual_bytes != resources["sram_bytes"]:
        raise ValueError(f"SRAM capacity disagrees with resources.json: {actual_bytes} bytes")
    return shapes, resources


def cacti_config(template, shape, temperature):
    width = shape["width_bits"]
    if width % 8:
        raise ValueError(f"CACTI adapter currently requires byte-wide SRAM words: {width} bits")
    settings = {
        r"^-size \(bytes\).*": f'-size (bytes) {shape["depth"] * width // 8}',
        r"^-block size \(bytes\).*": f'-block size (bytes) {width // 8}',
        r"^-associativity .*": "-associativity 1",
        r"^-read-write port .*": f'-read-write port {shape["rw_ports"]}',
        r"^-exclusive read port .*": f'-exclusive read port {shape["read_ports"]}',
        r"^-exclusive write port .*": f'-exclusive write port {shape["write_ports"]}',
        r"^-single ended read ports .*": "-single ended read ports 0",
        r"^-UCA bank count .*": "-UCA bank count 1",
        r"^-technology \(u\).*": "-technology (u) 0.045",
        r"^-output/input bus width .*": f"-output/input bus width {width}",
        r"^-operating temperature \(K\).*": f"-operating temperature (K) {temperature}",
        r"^-cache type .*": '-cache type "ram"',
        r"^-Data array cell type .*": '-Data array cell type - "itrs-hp"',
        r"^-Data array peripheral type .*": '-Data array peripheral type - "itrs-hp"',
        r"^-Add ECC .*": '-Add ECC - "false"',
        r"^-Optimize ED or ED\^2 .*": '-Optimize ED or ED^2 (ED, ED^2, NONE): "NONE"',
        r"^-design objective .*": "-design objective (weight delay, dynamic power, leakage power, cycle time, area) 0:0:0:0:100",
        r"^-deviate .*": "-deviate (delay, dynamic power, leakage power, cycle time, area) 100000:100000:100000:100000:100000",
        r"^-Cache model .*": '-Cache model (NUCA, UCA) - "UCA"',
        r"^-Force cache config .*": '-Force cache config - "false"',
    }
    for pattern, replacement in settings.items():
        template, count = re.subn(pattern, replacement, template, flags=re.M)
        if count != 1:
            raise ValueError(f"CACTI template needs exactly one active setting for {pattern!r}, found {count}")
    return template


def cacti_area(args, shape, template, tool_info):
    config = cacti_config(template, shape, args.temperature)
    key = fingerprint({"config": config, "tool": tool_info})
    output = args.output / "cacti" / key[:20]
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    if result_path.exists() and not args.force:
        result = json.loads(result_path.read_text())
        if result.get("fingerprint") == key:
            print(f"CACTI cache hit: {shape['depth']} x {shape['width_bits']}", flush=True)
            return result
    cfg = output / "sram.cfg"
    cfg.write_text(config)
    csv_path = output / "sram.cfg.out"
    if csv_path.exists():
        csv_path.unlink()  # CACTI appends CSV results; never reuse an old row.
    seconds = run([args.cacti, "-infile", cfg], args.cacti.parent, output / "cacti.log", args.timeout)
    with csv_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, skipinitialspace=True))
    if len(rows) != 1:
        raise ValueError(f"Expected one CACTI result row: {csv_path}")
    row = {k.strip(): v.strip() for k, v in rows[0].items() if k}
    metrics = {"area_mm2": "Area (mm2)", "access_time_ns": "Access time (ns)",
               "cycle_time_ns": "Random cycle time (ns)", "read_energy_nj": "Dynamic read energy (nJ)",
               "write_energy_nj": "Dynamic write energy (nJ)"}
    result = {name: float(row[column]) for name, column in metrics.items()}
    if not all(math.isfinite(x) and x > 0 for x in result.values()):
        raise ValueError(f"Invalid CACTI SRAM metrics: {result}")
    if (float(row["Tech node (nm)"]) != 45 or int(row["Capacity (bytes)"]) != shape["depth"] * shape["width_bits"] // 8
            or int(row["Output width (bits)"]) != shape["width_bits"] or int(row["Number of banks"]) != 1):
        raise ValueError("CACTI modeled the wrong SRAM geometry or technology")
    result.update(fingerprint=key, elapsed_seconds=seconds, config=str(cfg), log=str(output / "cacti.log"),
                  model="CACTI 45nm ITRS HP, one physical macro, no ECC, area objective",
                  temperature_kelvin=args.temperature,
                  offchip_io_report_used=False)
    write_json(result_path, result)
    return result


def logic_area(args, block, build, top, shapes, lib, tool_info):
    started = time.monotonic()
    output = args.output / block / "logic"
    output.mkdir(parents=True, exist_ok=True)
    sources = [Path(s) for s in (build / "synthesis.f").read_text().splitlines() if s.strip()]
    if any("top.mems.v" in str(s) for s in sources):
        raise ValueError("Behavioral SRAM models must not enter logic synthesis")
    front = [f"read_liberty -lib {quoted(lib)}"]
    front += [f"read_verilog -sv -DSYNTHESIS {quoted(s)}" for s in sources]
    front += [f"hierarchy -check -top {top}", f"synth -top {top} -noabc",
              f"write_rtlil {quoted(output / 'generic.il')}"]
    front_script = "\n".join(front) + "\n"
    front_inputs = {"script": front_script, "sources": [(str(s), digest(s)) for s in sources],
                    "liberty": digest(lib), "tool": tool_info}
    front_key = fingerprint(front_inputs)
    front_stamp = output / "generic-cache.json"
    front_cached = (not args.force and front_stamp.exists() and (output / "generic.il").is_file()
                    and json.loads(front_stamp.read_text()).get("fingerprint") == front_key)
    if front_cached:
        print(f"Yosys preprocessing cache hit: {block}", flush=True)
        front_seconds = json.loads(front_stamp.read_text())["elapsed_seconds"]
    else:
        (output / "generic.ys").write_text(front_script)
        front_seconds = run([args.yosys, "-Q", "-T", "-s", output / "generic.ys"],
                            ROOT, output / "generic.log", args.timeout)
        write_json(front_stamp, {"fingerprint": front_key, "elapsed_seconds": front_seconds})
    lines = [f"read_rtlil {quoted(output / 'generic.il')}", "chformal -remove", "opt -purge",
             f"dfflibmap -liberty {quoted(lib)}"]
    delay = f" -D {args.abc_delay_ps}" if args.abc_delay_ps is not None else ""
    # New Yosys releases removed `abc -fast`. An explicit short script provides
    # the same fast-mapping strategy across releases without SAT-based rewriting.
    abc_script = f"strash; dretime; map{delay}\n" if args.abc_quality == "fast" else None
    if abc_script:
        (output / "abc-fast.script").write_text(abc_script)
    abc_options = f" -script {quoted(output / 'abc-fast.script')}" if abc_script else delay
    lines += [f"abc -liberty {quoted(lib)}{abc_options}", "clean", "check -assert -mapped",
              f"tee -o {quoted(output / 'stat.json')} stat -json -liberty {quoted(lib)} -top {top}",
              f"write_json {quoted(output / 'mapped.json')}",
              f"write_verilog -noattr {quoted(output / 'mapped.v')}"]
    script = "\n".join(lines) + "\n"
    inputs = {"preprocessing": front_inputs, "preprocessing_fingerprint": front_key,
              "script": script, "abc_script": abc_script, "tool": tool_info}
    key = fingerprint(inputs)
    write_json(output / "inputs.json", inputs)
    stamp = output / "cache.json"
    cached = (not args.force and stamp.exists() and json.loads(stamp.read_text()).get("fingerprint") == key
              and (output / "mapped.json").is_file() and (output / "stat.json").is_file())
    if cached:
        print(f"Yosys cache hit: {block}", flush=True)
        seconds = json.loads(stamp.read_text())["elapsed_seconds"]
    else:
        (output / "synth.ys").write_text(script)
        seconds = run([args.yosys, "-Q", "-T", "-s", output / "synth.ys"], ROOT, output / "yosys.log", args.timeout)
        write_json(stamp, {"fingerprint": key, "elapsed_seconds": seconds})
    modules = json.loads((output / "mapped.json").read_text())["modules"]

    @functools.lru_cache(None)
    def leaves(name):
        if name in shapes:
            return collections.Counter(), collections.Counter({name: 1})
        module = modules.get(name)
        if module is None:
            raise ValueError(f"Unresolved mapped cell: {name}")
        attributes = module.get("attributes", {})
        if "area" in attributes:
            area = float(attributes["area"])
            if not math.isfinite(area) or area <= 0:
                raise ValueError(f"Invalid cell area: {name} = {area}")
            return collections.Counter({name: 1}), collections.Counter()
        if name.startswith("$") or int(attributes.get("blackbox", "0"), 2):
            raise ValueError(f"Unmapped logic or unaccounted blackbox: {name}")
        cells, srams = collections.Counter(), collections.Counter()
        for child, count in collections.Counter(c["type"] for c in module.get("cells", {}).values()).items():
            child_cells, child_srams = leaves(child)
            cells.update({k: v * count for k, v in child_cells.items()})
            srams.update({k: v * count for k, v in child_srams.items()})
        return cells, srams

    cells, srams = leaves(top)
    @functools.lru_cache(None)
    def rtl_instances(name):
        if name in shapes or "area" in modules[name].get("attributes", {}):
            return collections.Counter()
        counts = collections.Counter({re.sub(r"_\d+$", "", name): 1})
        for child, count in collections.Counter(c["type"] for c in modules[name].get("cells", {}).values()).items():
            counts.update({k: v * count for k, v in rtl_instances(child).items()})
        return counts

    retained = rtl_instances(top)
    required = {"AIETile": {"Gemmini": 1, "MacUnit": 1024, "FP32Simd": 1,
                            "FP32SimdEngine": 1, "FP32VectorRegisterFile": 1,
                            "AIETileSharedMemory": 1, "AIETileSramBank": 8,
                            "FP32SimdLane": 32, "MulAddRecFNPipe_l2_e8_s24": 32},
                "MemTile": {"ActiveSPM": 1, "ActiveSPMDMA": 1, "ActiveSPMConcurrentDMA": 1,
                            "ActiveSPMByteDMA": 1, "ActiveSPMOrderedAccess": 1},
                "InterconnectSwitch": {"Router": 2}}[top]
    if top == "MemTile":
        required["ActiveSPMDeSCAdmission"] = int(block == "mem-desc")
        required["Queue8_ActiveSPMDeSCBurst"] = 2 if block == "mem-desc" else 0
    for name, count in required.items():
        if retained[name] != count:
            raise ValueError(f"Expected {count} {name} instances after synthesis, found {retained[name]}")
    expected_srams = collections.Counter({name: s["instances"] for name, s in shapes.items()})
    if srams != expected_srams:
        raise ValueError(f"SRAM count changed in synthesis: expected {expected_srams}, found {srams}")
    raw_area = sum(float(modules[name]["attributes"]["area"]) * count for name, count in cells.items())
    stat_area = json.loads((output / "stat.json").read_text())["design"]["area"]
    if not cells or not math.isclose(raw_area, stat_area, rel_tol=1e-6, abs_tol=1e-3):
        raise ValueError(f"Hierarchical cell-area sum disagrees with Yosys stat: {raw_area} vs {stat_area}")
    area_um2 = raw_area * args.cell_area_scale
    subblocks = {}
    names = {}
    if top == "AIETile":
        # Direct-child contributions are disjoint; nested contributions are
        # explicitly reported as included in their parent, never added twice.
        names = {"Gemmini": top, "FP32SimdEngine": top, "FP32Simd": "FP32SimdEngine",
                 "FP32VectorRegisterFile": "FP32SimdEngine", "AIETileSharedMemory": top}
    elif top == "MemTile":
        names = {"ActiveSPMDMA": top, "ActiveSPMConcurrentDMA": "ActiveSPMDMA"}
        if block == "mem-desc":
            names["ActiveSPMDeSCAdmission"] = "ActiveSPMConcurrentDMA"
    for name, parent in names.items():
        matches = [n for n in modules if re.sub(r"_\d+$", "", n) == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one mapped module definition for {name}, found {matches}")
        child_cells, _ = leaves(matches[0])
        child_area = sum(float(modules[n]["attributes"]["area"]) * count
                         for n, count in child_cells.items()) * args.cell_area_scale
        subblocks[name] = {"area_um2": child_area, "area_mm2": child_area / 1e6, "included_in": parent}
    return {"area_um2": area_um2, "area_mm2": area_um2 / 1e6,
            "subblock_areas_included_in_total": subblocks,
            "standard_cell_instances": sum(cells.values()), "cell_counts": dict(cells),
            "rtl_instances_after_synthesis": dict(retained),
            "sram_instances_verified": dict(srams), "hierarchy_preserved": True,
            "abc_delay_target_ps": args.abc_delay_ps,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "preprocessing_original_seconds": front_seconds, "mapping_original_seconds": seconds,
            "preprocessing_cache_hit": front_cached,
            "abc_quality": args.abc_quality,
            "cache_hit": cached, "fingerprint": key, "log": str(output / "yosys.log"), "mapped_netlist": str(output / "mapped.v")}


def reports(args, results, provenance):
    report = {"technology_nm": 45, "modules": results, "provenance": provenance,
              "assumptions": [
                  "Logic is Liberty standard-cell area; SRAM is CACTI physical-macro area, each instance counted once.",
                  "Hierarchy is preserved; remaining small RTL memories/queues are mapped to standard cells.",
                  "Verification-only assert/assume/cover cells and their dead cones are removed before technology mapping.",
                  "CACTI ITRS HP SRAM and Nangate45 logic are separate predictive models, not one calibrated foundry PDK.",
                  "CACTI does not explicitly model the generated byte-write-mask circuitry; masks remain a macro-model approximation.",
                  "Sum excludes placement whitespace, routing, clock-tree synthesis, power grid, pads and external host infrastructure.",
                  "ABC delay target, if supplied, is a mapping hint; no timing closure or macro timing integration is performed.",
                  "AIETile includes a 32-lane FP32 unit, an 8x1024-bit flop vector register file, command scoreboard and bank arbitration sharing Gemmini SRAM. Instruction fetch/decode and shuffle networks are external.",
              ]}
    common = ("data_bits", "beat_bytes", "payload_bits", "virtual_channels", "buffer_depth",
              "local_scratchpad_bits", "output_queues", "mesh_columns", "mesh_rows",
              "mem_dma_in_flight", "mem_dma_buffer_bytes", "mem_dma_local_ports", "mem_dma_local_in_flight")
    report["interconnect"] = {k: next(iter(results.values()))["interconnect"][k] for k in common}
    if any(any(item["interconnect"][k] != report["interconnect"][k] for k in common) for item in results.values()):
        raise ValueError("Cannot combine blocks generated with different interconnect profiles")
    if {"mem", "mem-desc"} <= results.keys():
        naive, desc = results["mem"], results["mem-desc"]
        if naive["sram_macros"] != desc["sram_macros"]:
            # Ignore CACTI cache hit flags and output paths when comparing geometry.
            def geometry(item):
                keys = ("depth", "width_bits", "rw_ports", "read_ports", "write_ports", "instances")
                return sorted(tuple(macro[key] for key in keys) for macro in item["sram_macros"])
            if geometry(naive) != geometry(desc) or naive["sram_area_mm2"] != desc["sram_area_mm2"]:
                raise ValueError("Naive and DeSC MemTiles must use identical SRAM macros and area")
        delta = desc["logic"]["area_mm2"] - naive["logic"]["area_mm2"]
        tiles = report["interconnect"]["mesh_columns"]
        report["memtile_desc_comparison"] = {
            "instances": tiles, "logic_delta_mm2_per_tile": delta,
            "logic_delta_mm2_all_tiles": tiles * delta,
            "logic_delta_percent": 100 * delta / naive["logic"]["area_mm2"],
            "tile_delta_percent": 100 * delta / naive["total_area_mm2"],
        }
    if all(name in results for name in ("aie", "mem", "switch")):
        counts = {"aie": 32, "mem": 8, "switch": 40}
        report["array_uniform_switch_proxy"] = {
            "instances": counts,
            "logic_area_mm2": sum(results[k]["logic"]["area_mm2"] * n for k, n in counts.items()),
            "sram_area_mm2": sum(results[k]["sram_area_mm2"] * n for k, n in counts.items()),
            "total_area_mm2": sum(results[k]["total_area_mm2"] * n for k, n in counts.items()),
            "sram_bytes": sum(results[k]["sram_bytes"] * n for k, n in counts.items()),
            "int8_macs_per_cycle": 32 * results["aie"]["compute"]["int8_macs_per_cycle"],
            "fp32_fmas_per_cycle": 32 * results["aie"]["compute"]["fp32_fmas_per_cycle"],
            "fp32_flops_per_cycle": 32 * results["aie"]["compute"]["fp32_flops_per_cycle"],
            "assumption": "40 copies of the interior compute switch; boundary and memory-switch differences are not included in this proxy.",
        }
    write_json(args.output / "area-report.json", report)
    with (args.output / "area-report.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["module", "top", "logic_um2", "sram_um2", "total_um2", "logic_mm2", "sram_mm2", "total_mm2", "sram_bytes"])
        for name, item in results.items():
            logic, sram = item["logic"]["area_mm2"], item["sram_area_mm2"]
            writer.writerow([name, item["top"], logic * 1e6, sram * 1e6, (logic + sram) * 1e6,
                             logic, sram, logic + sram, item["sram_bytes"]])
    modes = ", ".join(sorted({item["logic"]["abc_quality"] for item in results.values()}))
    lines = ["# XDNA 2 area estimate at 45 nm", "", f"ABC mapping mode: {modes}. CACTI temperature: {args.temperature} K.", "",
             "Interconnect: **512-bit data / 577-bit NoC payload**, five VCs with two flits per VC. Native tile SRAM ports remain 256 bits.", "",
             "| Module | Logic (mm²) | SRAM (mm²) | Total (mm²) | SRAM (KiB) |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for name, item in results.items():
        lines.append(f"| {name} | {item['logic']['area_mm2']:.6f} | {item['sram_area_mm2']:.6f} | {item['total_area_mm2']:.6f} | {item['sram_bytes']/1024:g} |")
    if "aie" in results:
        aie = results["aie"]
        compute = aie["compute"]
        fp32 = aie["logic"]["subblock_areas_included_in_total"]["FP32Simd"]["area_mm2"]
        lines += ["", f"Each AIETile includes {compute['int8_macs_per_cycle']} INT8 MACs/cycle and "
                  f"{compute['fp32_fmas_per_cycle']} FP32 FMAs/cycle ({compute['fp32_flops_per_cycle']} FP32 FLOPs/cycle).",
                  f"The FP32 arithmetic core contributes **{fp32:.6f} mm²**, already included in AIE logic area.",
                  "The SIMD engine includes eight 1024-bit flop vector registers, a hazard scoreboard, completion buffering and four-beat vector LOAD/STORE transfers to the same scratchpad used by Gemmini.",
                  "Gemmini has priority on a busy SRAM bank. Arithmetic peak throughput assumes operands in the vector registers; SRAM transfers and dependencies can stall issue."]
    if "array_uniform_switch_proxy" in report:
        array = report["array_uniform_switch_proxy"]
        lines += ["", f"Uniform-switch array proxy (32 AIE + 8 Mem + 40 interior switches): **{array['total_area_mm2']:.6f} mm²**.",
                  "This proxy uses one switch type at every position; consult the location variants for more detailed accounting."]
    if "mem" in results or "mem-desc" in results:
        lines += ["", "MemTile keeps its address-mapped 512-KiB SRAM and original MMIO command semantics. "
                  "Its DMA has 64 external transaction slots, a shared 4-KiB flop data window, "
                  "and two private 256-bit SRAM paths with 16 local tags each. "
                  "A pipelined ordered global ingress preserves the FIFO contract without serializing every request. "
                  "The DMA flop window and queues are included in logic, not in the 512-KiB SRAM total."]
    if "mem-desc" in results:
        lines += ["", "DeSC uses two eight-entry descriptor queues and weighted admission at external DMA issue. "
                  "It reserves at least half of the shared DMA slots for inference. "
                  "Class credits and completion feedback cover aligned 64-byte transfers."]
    if "memtile_desc_comparison" in report:
        comparison = report["memtile_desc_comparison"]
        lines += ["", f"DeSC area increment per MemTile: **{comparison['logic_delta_mm2_per_tile']:+.6f} mm²** "
                  f"({comparison['tile_delta_percent']:+.2f}% of naive MemTile area). "
                  f"Total increment for {comparison['instances']} MemTiles: "
                  f"**{comparison['logic_delta_mm2_all_tiles']:+.6f} mm²**."]
    lines += ["", "## Assumptions", ""] + [f"- {line}" for line in report["assumptions"]]
    (args.output / "area-report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modules", nargs="+", choices=BLOCKS, default=["aie", "mem", "switch"])
    parser.add_argument("--output", type=Path, default=ROOT / "build/xdna2-area-45nm")
    parser.add_argument("--yosys", type=Path, default=Path.home() / "oss-cad-suite/bin/yosys")
    parser.add_argument("--cacti", type=Path, default=Path.home() / "cacti/cacti")
    parser.add_argument("--cacti-template", type=Path, help="Defaults to cache.cfg beside the CACTI executable")
    parser.add_argument("--liberty", type=Path, help="Use a local library instead of downloading pinned Nangate45 typical")
    parser.add_argument("--cell-area-scale", type=float, default=1.0, help="Multiply Liberty area by this factor to obtain um^2")
    parser.add_argument("--temperature", type=int, choices=range(300, 401, 10), default=300)
    parser.add_argument("--abc-delay-ps", type=float, help="Optional combinational mapping delay target in ps")
    parser.add_argument("--abc-quality", choices=["fast", "standard"], default="fast",
                        help="Fast area exploration by default; standard enables more expensive ABC optimization")
    parser.add_argument("--timeout", type=int, default=7200, help="Timeout in seconds for each tool invocation")
    parser.add_argument("--generate-rtl", action="store_true", help="Refresh selected RTL using the Chipyard make flow")
    parser.add_argument("--force", action="store_true", help="Ignore synthesis/CACTI result caches")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.yosys, args.cacti = args.yosys.resolve(), args.cacti.resolve()
    for path in (args.yosys, args.cacti):
        if not path.is_file() or not os.access(path, os.X_OK):
            parser.error(f"Executable not found: {path}")
    if not math.isfinite(args.cell_area_scale) or args.cell_area_scale <= 0:
        parser.error("--cell-area-scale must be finite and positive")
    if args.abc_delay_ps is not None and (not math.isfinite(args.abc_delay_ps) or args.abc_delay_ps <= 0):
        parser.error("--abc-delay-ps must be finite and positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    # Shared macro caches and summary files require a single writer per output.
    lock = (args.output / "workflow.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error(f"Another area workflow is using {args.output}; choose a different --output")
    lock.seek(0)
    lock.truncate()
    lock.write(f"{os.getpid()}\n")
    lock.flush()
    lib = liberty(args)
    cacti_template = (args.cacti_template or args.cacti.parent / "cache.cfg").resolve()
    template = cacti_template.read_text()
    tech_file = args.cacti.parent / "tech_params/45nm.dat"
    tool_info = {
        "yosys_version": subprocess.check_output([str(args.yosys), "-V"], text=True).strip(),
        "yosys_executable_sha256": digest(args.yosys), "cacti_executable_sha256": digest(args.cacti),
        "cacti_45nm_data_sha256": digest(tech_file),
    }
    # OSS CAD Suite bin/ entries are launcher scripts. Fingerprint the actual
    # engines too, so replacing ABC or a nightly bundle cannot reuse stale areas.
    suite = args.yosys.parent.parent
    if (suite / "VERSION").is_file() and (suite / "libexec/yosys").is_file():
        tool_info["oss_cad_suite_release"] = (suite / "VERSION").read_text().strip()
        tool_info["yosys_engine_sha256"] = digest(suite / "libexec/yosys")
        tool_info["abc_engine_sha256"] = digest(suite / "libexec/yosys-abc")
    provenance = dict(tool_info, liberty=str(lib), liberty_sha256=digest(lib),
                      liberty_area_scale_to_um2=args.cell_area_scale, cacti_template=str(cacti_template),
                      cacti_template_sha256=digest(cacti_template), cacti_temperature_kelvin=args.temperature)
    selected = list(dict.fromkeys(args.modules))
    generated = ROOT / "sims/verilator/generated-src"
    missing = [b for b in selected if not (generated / f"chipyard.xdna2.Xdna2AreaHarness.{BLOCKS[b][0]}" / "synthesis.f").exists()]
    if args.generate_rtl or missing:
        run([ROOT / "scripts/generate-xdna2-area-rtl.sh", *(selected if args.generate_rtl else missing)],
            ROOT, args.output / "rtl-generation.log", args.timeout)
    results = {}
    for block in selected:
        config, top = BLOCKS[block]
        build = generated / f"chipyard.xdna2.Xdna2AreaHarness.{config}"
        print(f"\nAnalyzing {block} ({top})", flush=True)
        shapes, resources = macro_shapes(build)
        profile = resources.get("interconnect", {})
        if any(profile.get(k) != v for k, v in {"data_bits": 512, "payload_bits": 577,
                                               "virtual_channels": 5, "buffer_depth": 2,
                                               "mem_dma_in_flight": 64, "mem_dma_buffer_bytes": 4096,
                                               "mem_dma_local_ports": 2, "mem_dma_local_in_flight": 16}.items()):
            raise ValueError("RTL lacks the current 512-bit network / concurrent MemTile DMA profile; regenerate it with --generate-rtl")
        if top == "MemTile" and profile.get("desc_queue_depth", 0) != (8 if block == "mem-desc" else 0):
            raise ValueError(f"Incorrect DeSC queue depth for {block}; regenerate RTL with --generate-rtl")
        if block == "aie" and resources.get("fp32_simd_lanes") != 32:
            raise ValueError("AIETile RTL lacks the required 32-lane FP32 SIMD; regenerate it with --generate-rtl")
        if block == "aie" and (not resources.get("simd_scratchpad_connected") or resources.get("fp32_vector_register_bytes") != 1024):
            raise ValueError("AIETile RTL lacks the SIMD register file/shared scratchpad; regenerate it with --generate-rtl")
        macros = []
        for name, shape in shapes.items():
            result = cacti_area(args, shape, template, tool_info)
            macros.append(dict(name=name, **shape, cacti=result,
                               total_area_mm2=result["area_mm2"] * shape["instances"]))
        logic = logic_area(args, block, build, top, shapes, lib, tool_info)
        sram_area = sum(m["total_area_mm2"] for m in macros)
        result = {"top": top, "config": config, "rtl_directory": str(build), "logic": logic,
                  "interconnect": profile,
                  "compute": {key: resources.get(key, 0) for key in (
                      "int8_macs_per_cycle", "fp32_simd_lanes", "fp32_fmas_per_cycle",
                      "fp32_flops_per_cycle", "fp32_latency_cycles", "fp32_vector_registers",
                      "fp32_vector_register_bytes", "simd_scratchpad_connected")},
                  "sram_macros": macros, "sram_bytes": resources["sram_bytes"],
                  "sram_area_mm2": sram_area, "total_area_mm2": logic["area_mm2"] + sram_area}
        results[block] = result
        write_json(args.output / block / "area.json", result)
        print(f"{block}: logic {logic['area_mm2']:.6f} + SRAM {sram_area:.6f} = {result['total_area_mm2']:.6f} mm^2", flush=True)
        reports(args, results, provenance)
    if all(name in results for name in ("aie", "mem", "switch")):
        run([sys.executable, ROOT / "scripts/report-xdna2-area-breakdown.py", "--area-dir", args.output],
            ROOT, args.output / "area-table-generation.log", args.timeout)
    print(f"\nReports: {args.output / 'area-report.md'}", flush=True)


if __name__ == "__main__":
    main()
