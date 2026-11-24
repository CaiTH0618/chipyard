# 45 nm NPU area results

The [results archive](results/) contains the latest area measurement for each
of the seven standalone configurations. The combined report uses the latest
AIE and switch measurements and the matched naive and DeSC Mem Tile run.
Original tool outputs are preserved byte for byte. Git preserves the original
line endings so that the recorded checksums hold after checkout.

| Archive | Contents |
| --- | --- |
| [xdna2-area-45nm](results/xdna2-area-45nm/) | [Combined area report](results/xdna2-area-45nm/area-report.md), raw statistics for all seven blocks, the current [naive NPU breakdown](results/xdna2-area-45nm/npu-area-table.md), and the detailed CSV breakdown and plotting script. |
| [rtl-metadata](results/rtl-metadata/) | Resources, macro geometry, profiles and instance hierarchies for the measured configurations. Originating files are under `sims/verilator/generated-src/chipyard.xdna2.Xdna2AreaHarness.<config>/`. |
| [Verification records](results/) | The `xdna2-*-rtl-check/` directories hold the RTL test results. [The Mem Tile run log](results/xdna2-desc-validation/area.log) records the final comparison. |

Each measured block has `area.json`, the original Yosys
`logic/stat.json` with standard-cell area and counts, and
`logic/inputs.json` with the RTL SHA-256 hashes and tool and library
fingerprints. The generated `generic.ys`, `synth.ys` and ABC script record the
tool settings. Each of the three CACTI macro records retains `sram.cfg`,
the original `sram.cfg.out` CSV, the tool's `cacti.log` and the parsed
`result.json`. Both Mem Tile variants use the same 2048 × 256 SRAM record.
The macro multiplicities come from the area and resource reports.

The `measurement_sources` field of
[area-report.json](results/xdna2-area-45nm/area-report.json) records the
original build directory for each block. The original tool outputs retain
their local paths. Within this archive, look up CACTI records by the first
20 characters of the macro fingerprint. Together with the measured input
hashes, the RTL metadata identifies the configurations used for the report.

## Area accounting

The final runs use Yosys 0.70+385 from OSS CAD Suite 20261112, fast ABC
mapping and Nangate45 typical cells at 1.1 V and 25 °C. The Liberty revision
and checksum are saved in
[`tech/source.json`](results/xdna2-area-45nm/tech/source.json). The reports
contain both synthesis tool hashes and CACTI executable, template and
technology-data hashes. CACTI models 45 nm SRAM at 300 K with one physical
bank per macro, no ECC and an area objective.

Yosys `design.area` is in µm² for this Liberty. Divide by 1,000,000 to obtain
mm². For SRAM, multiply the raw CACTI `Area (mm2)` per macro by the number of
physical instances. The NPU proxy counts 32 AIE Tiles, eight Mem Tiles and
40 copies of the measured interior logical switch. It estimates standard
cells plus SRAM macros. Floorplan, wiring, clock trees and host
infrastructure fall outside that area measure.

| Mem Tile in the matched run | Logic (mm²) | SRAM (mm²) | Total (mm²) |
| --- | ---: | ---: | ---: |
| Naive | 0.616858522 | 1.530424000 | 2.147282522 |
| DeSC | 0.637215236 | 1.530424000 | 2.167639236 |

The measured DeSC DMA control increment is
`8 × (0.637215236 − 0.616858522) = 0.162853712 mm²`. The paper table uses
the latest naive logic area for L2 Others, the measured Cores, L2 SRAM and
NoC areas, and the manuscript's decoder engine
breakdown of 9.0140 mm² arithmetic, 2.6222 mm² SRAM and 0.7327 mm² other
logic. The manuscript provides the decoder engine's area summary. The raw
Chipyard records here cover the NPU blocks and DeSC DMA control.

| Component | Module | Module area (mm²) | Component area (mm²) | % |
| --- | --- | ---: | ---: | ---: |
| Cores | Matrix unit | 60.1383 | 139.5806 | 72.64 |
| | Vector unit | 16.7902 | | |
| | SRAM | 24.9572 | | |
| | Others | 37.6949 | | |
| L2 | L2 SRAM | 12.2434 | 17.3411 | 9.02 |
| | Others | 4.9349 | | |
| | DeSC DMA control | 0.1629 | | |
| Decoding engine | Arithmetic | 9.0140 | 12.3689 | 6.44 |
| | SRAM | 2.6222 | | |
| | Others | 0.7327 | | |
| NoC | Router | 22.8703 | 22.8703 | 11.90 |
| Total | | | **192.1609** | **100.00** |

The naive NPU with the remeasured Mem Tile is 179.629133328 mm².
Adding 12.3689 mm² for the decoding engine and 0.162853712 mm² for DMA
control gives 192.160887040 mm² and **6.98%** area overhead. Calculate shares
and subtotals before rounding the displayed values.

## Verify and reproduce

Verify the archive without rebuilding or mapping RTL.

```sh
(cd docs/npu-area/results && sha256sum --check --quiet SHA256SUMS)
```

Regenerate the detailed array report and figures from the stored statistics
in a local build directory.

```sh
mkdir -p build/xdna2-area-report-replay
cp -a docs/npu-area/results/xdna2-area-45nm/. build/xdna2-area-report-replay/
python3 scripts/report-xdna2-area-breakdown.py --area-dir build/xdna2-area-report-replay
source env.sh && source scripts/chipyard-build-resources.sh
python3 build/xdna2-area-report-replay/generate-full-breakdown.py
```

The plotting script uses Matplotlib. For a fresh area measurement of the
current source tree, follow the [RTL and area workflow](../../generators/chipyard/src/main/scala/xdna2/README.md).
For example, rerun the matched Mem Tile comparison with the pinned library.

```sh
scripts/run-xdna2-area-45nm.sh --modules mem mem-desc --generate-rtl \
  --output build/xdna2-desc-area-repeat
```

The archive holds measurement data, input metadata, reports and the small
number of tool logs that record the results. Generated HDL, behavioral
memory models, mapped netlists, synthesis checkpoints, tool caches,
simulation executables and waveforms live in the ignored build directories.
