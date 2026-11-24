#!/usr/bin/env python3
"""Partition the measured 45-nm area into the requested Core/L2/NoC table."""

import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def hierarchy_areas(directory, top, scale):
    stat = json.loads((directory / "logic/stat.json").read_text())
    modules = {name.removeprefix("\\"): mod for name, mod in stat["modules"].items()}
    counts = collections.Counter()

    def walk(name, count=1):
        if name not in modules:
            return
        counts[name] += count
        for child, instances in modules[name]["num_cells_by_type"].items():
            if child in modules:
                walk(child, count * instances)

    walk(top)

    def area(base):
        matching = [name for name in counts if re.sub(r"_\d+$", "", name) == base]
        if not matching:
            raise ValueError(f"Missing {base} in {top}")
        return sum(modules[name]["area"] * scale * counts[name] for name in matching)

    return area


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area-dir", type=Path, default=ROOT / "build/xdna2-area-45nm")
    args = parser.parse_args()
    directory = args.area_dir.resolve()
    source = directory / "area-report.json"
    report = json.loads(source.read_text())
    modules = report["modules"]
    array = report["array_uniform_switch_proxy"]
    counts = array["instances"]
    total = array["total_area_mm2"]
    scale = report["provenance"]["liberty_area_scale_to_um2"] / 1e6
    aie_area = hierarchy_areas(directory / "aie", "AIETile", scale)
    me = aie_area("MeshWithDelays")
    ve = aie_area("FP32SimdEngine")
    others = modules["aie"]["logic"]["area_mm2"] - me - ve
    assert others >= 0
    rows = []

    def row(component, module, number, area):
        rows.append(dict(component=component, module=module, number=number,
                         area_mm2=area, percent=100 * area / total))

    row("Core", "ME", str(counts["aie"]), counts["aie"] * me)
    row("Core", "VE", str(counts["aie"]), counts["aie"] * ve)
    row("Core", "SRAM", "32 × 128 KiB", counts["aie"] * modules["aie"]["sram_area_mm2"])
    row("Core", "others", "/", counts["aie"] * others)
    row("L2", "L2 SRAM", "8 × 512 KiB", counts["mem"] * modules["mem"]["sram_area_mm2"])
    row("L2", "others", "/", counts["mem"] * modules["mem"]["logic"]["area_mm2"])
    row("NoC", "Switch", "5 × 8", counts["switch"] * modules["switch"]["total_area_mm2"])
    assert math.isclose(sum(r["area_mm2"] for r in rows), total, abs_tol=1e-8)
    result = dict(technology_nm=45, total_area_mm2=total, rows=rows,
                  source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  definitions={"ME": "INT8 MeshWithDelays including MACs, alignment, transpose and pipelines",
                               "VE": "Complete FP32SimdEngine including arithmetic, vector registers and control",
                               "Core others": "All remaining AIETile logic, including Gemmini DMA/control and shared-memory arbitration",
                               "L2 others": "All MemTile logic, including concurrent/byte DMA, flop buffers, ordered ingress, bank and TL adapters",
                               "NoC": array["assumption"]},
                  mem_dma_profile={k: v for k, v in report["interconnect"].items() if k.startswith("mem_dma_")})
    baseline = directory / "single-request-dma-baseline-area-report.json"
    if baseline.exists():
        old = json.loads(baseline.read_text())
        old_total = old["array_uniform_switch_proxy"]["total_area_mm2"]
        result["comparison"] = dict(baseline_total_area_mm2=old_total,
                                    delta_area_mm2=total-old_total, delta_percent=100*(total/old_total-1),
                                    mem_logic_before_mm2=old["modules"]["mem"]["logic"]["area_mm2"],
                                    mem_logic_after_mm2=modules["mem"]["logic"]["area_mm2"],
                                    switch_before_mm2=old["modules"]["switch"]["total_area_mm2"],
                                    switch_after_mm2=modules["switch"]["total_area_mm2"])
    (directory / "npu-area-table.json").write_text(json.dumps(result, indent=2) + "\n")
    with (directory / "npu-area-table.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows + [dict(component="Total", module="/", number="/", area_mm2=total, percent=100)])
    lines = ["45 nm: Nangate45 standard cells + CACTI SRAM; each row sums all its instances.", "",
             "| Component | Module | Num. | Area (mm²) | % |",
             "| --- | --- | ---: | ---: | ---: |"]
    previous = None
    for r in rows:
        component = r["component"] if r["component"] != previous else ""
        lines.append(f"| {component} | {r['module']} | {r['number']} | {r['area_mm2']:.4f} | {r['percent']:.2f} |")
        previous = r["component"]
    lines += [f"| **Total** | / | / | **{total:.4f}** | **100.00** |", "",
              "ME includes the INT8 array and its alignment/transpose/pipeline fabric. "
              "VE includes the full SIMD engine and vector register file. "
              "SRAM excludes flop queues/data buffers, which are included in logic.", "",
              "NoC uses 40 copies of the interior logical switch (each contains two routers and local interfaces); "
              "it is not a position-exact mesh sum. Placement whitespace, routing, CTS and host infrastructure are excluded."]
    if "comparison" in result:
        c = result["comparison"]
        lines += ["", f"Previous: {c['baseline_total_area_mm2']:.4f} mm². "
                  f"Change: {c['delta_area_mm2']:+.4f} mm² ({c['delta_percent']:+.2f}%)."]
    (directory / "npu-area-table.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
