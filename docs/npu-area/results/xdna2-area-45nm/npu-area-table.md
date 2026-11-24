45 nm: Nangate45 standard cells + CACTI SRAM; each row sums all its instances.

| Component | Module | Num. | Area (mm²) | % |
| --- | --- | ---: | ---: | ---: |
| Core | ME | 32 | 60.1383 | 33.48 |
|  | VE | 32 | 16.7902 | 9.35 |
|  | SRAM | 32 × 128 KiB | 24.9572 | 13.89 |
|  | others | / | 37.6949 | 20.98 |
| L2 | L2 SRAM | 8 × 512 KiB | 12.2434 | 6.82 |
|  | others | / | 4.9349 | 2.75 |
| NoC | Switch | 5 × 8 | 22.8703 | 12.73 |
| **Total** | / | / | **179.6291** | **100.00** |

ME includes the INT8 array and its alignment/transpose/pipeline fabric. VE includes the full SIMD engine and vector register file. SRAM excludes flop queues/data buffers, which are included in logic.

NoC uses 40 copies of the interior logical switch (each contains two routers and local interfaces); it is not a position-exact mesh sum. Placement whitespace, routing, CTS and host infrastructure are excluded.
