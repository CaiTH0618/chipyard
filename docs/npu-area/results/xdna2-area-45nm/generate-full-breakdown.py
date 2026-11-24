#!/usr/bin/env python3
"""Report existing mapped areas without re-running synthesis or simulation."""
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
report = json.loads((ROOT / "area-report.json").read_text())
total = report["array_uniform_switch_proxy"]["total_area_mm2"]
scale = report["provenance"]["liberty_area_scale_to_um2"] / 1e6
inventory = {"aie": 32, "mem": 8, "switch": 40}
stats = {}
counts = {}
for block, top in (("aie", "AIETile"), ("mem", "MemTile"), ("switch", "InterconnectSwitch")):
    stat = json.loads((ROOT / block / "logic/stat.json").read_text())
    stats[block] = {k.removeprefix("\\"): v for k, v in stat["modules"].items()}
    assert math.isclose(stat["design"]["area"] * scale,
                        report["modules"][block]["logic"]["area_mm2"], abs_tol=1e-9)
    counts[block] = collections.Counter()
    def walk(name, multiplicity=1):
        if name not in stats[block]:
            return
        counts[block][name] += multiplicity
        for child, count in stats[block][name]["num_cells_by_type"].items():
            if child in stats[block]:
                walk(child, multiplicity * count)
    walk(top)

def area(block, base):
    matches = [name for name in counts[block] if re.sub(r"_\d+$", "", name) == base]
    if not matches:
        raise ValueError((block, base))
    return sum(stats[block][name].get("area", 0.0) * scale * counts[block][name] for name in matches)

def sram_area(block, depth, bits):
    macros = [m for m in report["modules"][block]["sram_macros"]
              if m["depth"] == depth and m["width_bits"] == bits]
    assert len(macros) == 1
    return macros[0]["total_area_mm2"]

def entry(block, component, value, kind="logic"):
    assert value >= -1e-9, (component, value)
    return {"block": block, "component": component, "kind": kind,
            "per_block_mm2": value, "instances": inventory[block],
            "array_mm2": value * inventory[block], "percent_of_array": value * inventory[block] / total * 100}

mac = area("aie", "MacUnit")
mesh = area("aie", "MeshWithDelays")
gemmini = area("aie", "Gemmini")
fp32 = area("aie", "FP32Simd")
vrf = area("aie", "FP32VectorRegisterFile")
simd_control = area("aie", "FP32SimdEngine") - fp32 - vrf
shared = area("aie", "AIETileSharedMemory")
aie_sp = sram_area("aie", 512, 256)
aie_acc = sram_area("aie", 128, 1024)
major = [
    entry("aie", "INT8 systolic array, alignment/transposer and pipelines", mesh),
    entry("aie", "Other Gemmini logic: DMA, storage control, scheduling and command loops", gemmini - mesh),
    entry("aie", "FP32 arithmetic and its pipeline", fp32),
    entry("aie", "FP32 vector register file, including read/write muxes", vrf),
    entry("aie", "SIMD command engine, scoreboard and completions", simd_control),
    entry("aie", "Shared SRAM bank arbitration and access glue", shared),
    entry("aie", "AIE scratchpad SRAM", aie_sp, "sram"),
    entry("aie", "AIE accumulator SRAM", aie_acc, "sram"),
    entry("mem", "MemTile DMA, control, bank interfaces and network adapters", report["modules"]["mem"]["logic"]["area_mm2"]),
    entry("mem", "MemTile SRAM", report["modules"]["mem"]["sram_area_mm2"], "sram"),
    entry("switch", "NoC router pairs and local protocol interfaces", report["modules"]["switch"]["total_area_mm2"]),
]
aie = [
    entry("aie", "INT8 MAC arithmetic (1024 units)", mac),
    entry("aie", "INT8 fabric, alignment/transposer, PE registers and pipelines", mesh - mac),
    entry("aie", "Execute controller outside array/pipelines", area("aie", "ExecuteController") - mesh),
    entry("aie", "Scratchpad/accumulator control, adders, data paths and queues (excluding DMA engines)",
          area("aie", "Scratchpad") - area("aie", "StreamReader") - area("aie", "StreamWriter")),
    entry("aie", "Gemmini DMA stream reader and tracking", area("aie", "StreamReader")),
    entry("aie", "Gemmini DMA stream writer", area("aie", "StreamWriter")),
    entry("aie", "Load/store command controllers", area("aie", "LoadController") + area("aie", "StoreController")),
    entry("aie", "Reservation station", area("aie", "ReservationStation")),
    entry("aie", "Matmul loop command controller", area("aie", "LoopMatmul")),
    entry("aie", "Convolution loop command controller", area("aie", "LoopConv")),
    entry("aie", "Frontend TLB and protection checks", area("aie", "FrontendTLB")),
    entry("aie", "Im2Col unit", area("aie", "Im2Col")),
    entry("aie", "Performance counters", area("aie", "CounterController")),
]
aie.append(entry("aie", "Other Gemmini command queues, arbiters and glue", gemmini - sum(r["per_block_mm2"] for r in aie)))
aie += major[2:8]
mem_stats = stats["mem"]
mem_xbar = sum(mem_stats[name]["area"] * scale * counts["mem"][name]
               for name in counts["mem"] if name.startswith("TLXbar_"))
mem_order = area("mem", "ActiveSPMOrderedAccess")
mem_banks = area("mem", "TLRAM")
mem = [entry("mem", "MemTile DMA including concurrent/byte engines and local/external buffers", area("mem", "ActiveSPMDMA")),
       entry("mem", "MemTile control registers", area("mem", "ActiveSPMControl")),
       entry("mem", "MemTile bank crossbar and arbitration", mem_xbar),
       entry("mem", "MemTile SRAM bank access logic and response registers", mem_banks),
       entry("mem", "MemTile pipelined ordered global ingress", mem_order),
       entry("mem", "MemTile bank response queues", area("mem", "ActiveSPMScratchpad") - mem_xbar - mem_order - mem_banks)]
for name, label in (("TLWidthWidget64", "MemTile 512-to-256-bit width adaptation"),
                    ("TLFragmenter", "MemTile request fragmentation"),
                    ("TLFIFOFixer", "MemTile outer FIFO adapter"),
                    ("TLSourceShrinker", "MemTile source-ID remapping")):
    matches = [n for n in counts["mem"] if re.sub(r"_\d+$", "", n) == name]
    value = area("mem", name) if matches else 0.0
    mem.append(entry("mem", label, value))

mem.append(major[9])
switch = [
    entry("switch", "NoC input-buffer storage, muxes and per-VC queues", area("switch", "InputBuffer")),
    entry("switch", "NoC payload crossbars", area("switch", "Switch")),
    entry("switch", "NoC route computation, VC allocation and switch allocation",
          sum(area("switch", n) for n in ("RouteComputer", "RotatingSingleVCAllocator", "SwitchAllocator"))),
    entry("switch", "NoC output/credit control", area("switch", "OutputUnit")),
    entry("switch", "NoC local terminal ingress/egress units",
          area("switch", "IngressUnit") + area("switch", "EgressUnit")),
    entry("switch", "NoC local TileLink packet interface", area("switch", "TLMasterToNoC")),
]
switch.append(entry("switch", "NoC remaining input-unit/router state and control",
                    report["modules"]["switch"]["total_area_mm2"] - sum(r["per_block_mm2"] for r in switch)))
detail = aie + mem + switch
for block, rows in (("aie", aie), ("mem", mem), ("switch", switch)):
    assert math.isclose(sum(r["per_block_mm2"] for r in rows), report["modules"][block]["total_area_mm2"], abs_tol=1e-9)
for rows in (major, detail):
    assert math.isclose(sum(r["array_mm2"] for r in rows), total, abs_tol=1e-8)

memory = []
for block in ("aie", "mem"):
    for m in report["modules"][block]["sram_macros"]:
        memory.append({"block": block, "depth": m["depth"], "width_bits": m["width_bits"],
                       "capacity_bytes_each": m["depth"] * m["width_bits"] // 8,
                       "macros_per_block": m["instances"], "macros_in_array": inventory[block] * m["instances"],
                       "area_mm2_each": m["cacti"]["area_mm2"],
                       "area_mm2_array": m["total_area_mm2"] * inventory[block]})
assert sum(m["macros_in_array"] for m in memory) == 320
assert sum(m["capacity_bytes_each"] * m["macros_in_array"] for m in memory) == 8*1024*1024
result = {"technology_nm": 45, "interconnect": report["interconnect"], "inventory": inventory,
          "total_mm2": total, "logic_mm2": report["array_uniform_switch_proxy"]["logic_area_mm2"],
          "sram_mm2": report["array_uniform_switch_proxy"]["sram_area_mm2"],
          "major_partition": major, "detailed_partition": detail, "physical_sram_macros": memory,
          "note": "Each partition separately sums to the total; do not add the major and detailed partitions together.",
          "noc_accounting": {"method": "40 copies of the measured interior switch at (3,2)",
              "mesh_geometry": {"interior": 18, "noncorner_edge": 18, "corner": 4},
              "location_samples_mm2": {name: report["modules"][name]["total_area_mm2"]
                                        for name in report["modules"] if name.startswith("switch")},
              "position_exact": False,
              "limitation": "The four samples do not cover every coordinate or external host/DRAM/control interface; this is not an upper/lower bound."},
          "standard_cell_instances": sum(report["modules"][k]["logic"]["standard_cell_instances"] * n for k,n in inventory.items()),
          "flip_flop_instances": sum(report["modules"][k]["logic"]["cell_counts"]["DFF_X1"] * n for k,n in inventory.items()),
          "source_report": str(ROOT / "area-report.json"),
          "source_report_sha256": hashlib.sha256((ROOT / "area-report.json").read_bytes()).hexdigest(),
          "provenance": report["provenance"], "assumptions": report["assumptions"],
          "validation": {"all_per_block_sums_match": True, "major_partition_matches_array": True,
                         "detailed_partition_matches_array": True, "sram_macros": 320, "sram_bytes": 8*1024*1024,
                         "new_synthesis_or_simulation_run": False}}
(ROOT / "full-area-breakdown.json").write_text(json.dumps(result, indent=2) + "\n")
for filename, rows in (("full-area-breakdown.csv", detail), ("full-area-summary.csv", major)):
    with (ROOT / filename).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
groups = [(major[0]["component"], major[0]["array_mm2"]),
          ("Gemmini control, DMA and storage peripherals", major[1]["array_mm2"]),
          ("All physical SRAM macros (8 MiB)", result["sram_mm2"]),
          ("NoC routers and local interfaces", major[10]["array_mm2"]),
          ("FP32 arithmetic and pipelines", major[2]["array_mm2"]),
          ("FP32 vector registers and access muxes", major[3]["array_mm2"]),
          ("MemTile DMA, control and adapters", major[8]["array_mm2"]),
          ("SIMD command control and shared SRAM arbitration", major[4]["array_mm2"] + major[5]["array_mm2"])]
groups.sort(key=lambda pair: pair[1], reverse=True)
assert math.isclose(sum(value for _,value in groups), total, abs_tol=1e-8)
fig, ax = plt.subplots(figsize=(13, 6.8))
values = [v for _,v in groups]
bars = ax.barh(range(len(groups)), values, color=["#305c91", "#547fa8", "#bd8337", "#458985", "#765d9d", "#9c82b5", "#7f8c9c", "#a5aeb8"], height=.65)
ax.set_yticks(range(len(groups)))
ax.set_yticklabels([name for name,_ in groups], fontsize=10)
ax.invert_yaxis()
for bar, value in zip(bars, values):
    ax.text(value + .6, bar.get_y() + bar.get_height()/2, f"{value:.2f} mm²  ({100*value/total:.2f}%)", va="center", fontsize=10)
ax.set_xlim(0, max(values)*1.36)
ax.set_xlabel("Standard-cell + SRAM-macro area (mm²)")
ax.set_title(f"NPU area breakdown at 45 nm — {total:.2f} mm²", loc="left", fontsize=16, pad=20)
ax.xaxis.grid(True, alpha=.18)
ax.set_axisbelow(True)
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
ax.tick_params(axis="y", length=0)
fig.text(.025, .02, "32 AIE + 8 Mem + 40 interior-switch proxies | Nangate45 typical + CACTI | Excludes placement/routing/CTS/host area", fontsize=9, color="#555555")
fig.subplots_adjust(left=.40, right=.97, bottom=.12, top=.88)
fig.savefig(ROOT / "full-area-breakdown.png", dpi=170, facecolor="white")
fig.savefig(ROOT / "full-area-breakdown.svg", facecolor="white")
plt.close(fig)
print(json.dumps({"total_mm2": total, "major_rows": len(major), "detailed_rows": len(detail),
                  "standard_cells": result["standard_cell_instances"], "flip_flops": result["flip_flop_instances"],
                  "validation": result["validation"]}, indent=2))
for row in major:
    print(f"{row['component']}: unit={row['per_block_mm2']:.9f}, array={row['array_mm2']:.9f}, share={row['percent_of_array']:.3f}%")
