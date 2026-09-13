# ActiveSPM Development Target

## Introduction

This document defines the development target of the `ActiveSPM` module.

An ActiveSPM instance contains a DMA engine and a scratchpad. The scratchpad is mapped into the TileLink address space so that CPUs and accelerators can access it as memory. The MMIO-controlled DMA copies data between the local scratchpad and external memory.

The target NPU subsystem contains multiple RocketCore, Gemmini and ActiveSPM instances connected through the system buses and a NoC. Each RocketCore controls one Gemmini through RoCC and controls ActiveSPM instances through MMIO. Gemmini uses its existing DMA to access the globally mapped ActiveSPM scratchpads through SBus. Each ActiveSPM DMA independently moves data between its local scratchpad and external memory.

## Implementation

### Code Directory and Configuration

- `ActiveSPM` will be a generator project at `generators/activespm`. It will contain the Scala implementation, Scala tests, C tests and example C programs.
- The generator will provide `ActiveSPMParams`, a sequence-valued configuration key, a subsystem attachment trait and Config fragments for creating multiple instances.
- `ActiveSPMParams` will include the instance ID, control and scratchpad address ranges, beat bytes, bank count and allowed external-memory address ranges.
- Instance addresses will be explicit configuration parameters. The instance ID will be used for identification and node naming, not for implicit address calculation.
- Test and example configs will be at `generators/chipyard/src/main/scala/config`.

### DMA Function

- The DMA will copy data only between external memory and its local scratchpad. External-to-external and local-to-local copies are not supported.
- A request contains a direction, a 64-bit external physical address, a 64-bit local scratchpad offset and a 64-bit byte count. The first implementation has no completion tag.
- Direction `0` loads external memory into the scratchpad. Direction `1` stores scratchpad data into external memory.
- The DMA will not contain a TLB or IOMMU. Software must provide a physical TileLink address. A pointer may be used directly only with a known identity mapping, as in the intended bare-metal environment.
- The DMA will implement complete byte-accurate `memcpy` semantics. The external address, local offset and byte count may have arbitrary alignment, including different byte-lane offsets on the two sides.
- An internal byte realigner and bounded ready/valid buffer will shift and combine data between source and destination beats. Partial destination beats will use byte masks and preserve bytes outside the requested range.
- Source reads and destination writes must remain entirely within the requested byte ranges. The DMA must use smaller aligned TileLink operations where a larger `Get` would read before or after the requested source range.
- A zero-length request will complete successfully without issuing a TileLink transaction.
- The first implementation will execute one software request at a time and allow at most one outstanding transaction on each of the external-memory and internal-scratchpad interfaces. Additional outstanding transactions and request formats are later extensions.

### MMIO ABI

- The first implementation will use polling and will not expose an interrupt.
- The control interface is a 64-bit MMIO register block. Software must use aligned 64-bit accesses.
- The register map is:

| Offset | Name | Access | Definition |
| ---: | --- | --- | --- |
| `0x00` | `COMMAND` | WO | Bit 0 is `START`; bit 1 is `DIRECTION`; other bits are reserved and must be zero. |
| `0x08` | `EXTERNAL_ADDR` | RW | External physical byte address. |
| `0x10` | `LOCAL_OFFSET` | RW | Byte offset relative to this ActiveSPM scratchpad base. |
| `0x18` | `BYTE_COUNT` | RW | Number of bytes to copy. |
| `0x20` | `STATUS` | RO/W1C | Bit 0 is `BUSY`; bit 1 is sticky `DONE`; bit 2 is sticky `ERROR`. Writing one to `DONE` or `ERROR` clears that bit. |
| `0x28` | `BYTES_COMPLETED` | RO | Length of the contiguous destination prefix whose writes have completed. |
| `0x30` | `ERROR_CODE` | RO | Error code described below. |

- Error code `0` is `NONE`, `1` is `BUSY`, `2` is `LOCAL_RANGE`, `3` is `ADDRESS_OVERFLOW`, `4` is `EXTERNAL_RANGE` and `5` is `TILELINK`.
- Writing `COMMAND` with `START=0` has no effect. Writing it with `START=1` while idle atomically captures `DIRECTION` and the three request registers, clears stale completion and error state, resets `BYTES_COMPLETED` to zero and starts the request.
- Writing `START` while busy is ignored, leaves the active request unchanged and sets `ERROR` with the `BUSY` error code. The active request continues normally.
- Descriptor registers may be written while busy, but such writes do not modify the captured active request.
- `DONE` is set only for successful completion. `BUSY` is cleared when the active transfer succeeds or fails; a rejected `START` does not clear it.
- Clearing `ERROR` also returns `ERROR_CODE` to `NONE`. An error produced by the active transfer takes precedence over an earlier `BUSY` error.
- `DONE` and `ERROR` may both be set only when a second `START` was rejected and the original active transfer later completed successfully.
- Completion requires every destination write to receive its TileLink response. Observing `DONE` therefore means that the copied bytes are visible at the destination interface.
- Unassigned command bits and MMIO offsets are reserved for compatible extensions.

### DMA Error Behavior

- Before issuing the first TileLink request, the DMA will reject local out-of-range accesses, address addition overflow and requests outside the configured external-memory ranges.
- TileLink `denied` and `corrupt` responses will both report the single `TILELINK` error code.
- On a TileLink error, the DMA will stop issuing new transactions, drain transactions already issued, clear `BUSY` and set `ERROR`. It will not write data returned with an error or roll back data already written by earlier transactions.
- `BYTES_COMPLETED` reports only the contiguous destination prefix completed before the error. Software must treat the destination as partially modified after any failed nonzero-length request.
- Reset aborts an active request. Partial source or destination contents are unspecified after such an abort, and the reset state is idle with status bits cleared.

### Scratchpad and Internal Data Path

- Each ActiveSPM instance will expose one contiguous, non-overlapping TileLink address region. Its base address and size are configuration parameters.
- The scratchpad will be non-cacheable, non-executable and non-atomic. It will support only TileLink `Get`, `PutFullData` and `PutPartialData` operations.
- The storage will be divided into a configurable power-of-two number of banks. Every bank will be implemented directly with `TLRAM` or its thin ActiveSPM wrapper.
- Banks are interleaved at the native scratchpad beat granularity, with bank selection `(localOffset / spadBeatBytes) % nBanks`. `spadBeatBytes`, the bank count and scratchpad size must be powers of two; the scratchpad base must be aligned to its size; and the scratchpad must contain at least one beat per bank.
- The external scratchpad manager and the DMA local client will meet at internal bank-level TileLink crossbars. The DMA-to-scratchpad path is a high-bandwidth internal path and is not exposed to SBus, the global NoC or NoC mapping.
- Different banks may operate concurrently. Requests targeting the same bank will be serialized with fair arbitration and backpressure.
- The global and local paths observe the same storage. No cache or private copy may be inserted on either path, and partial writes must preserve every unmasked byte.
- Scratchpad contents are not initialized or guaranteed to be zero after reset.
- One aggregated scratchpad data manager will be visible outside each instance. Individual bank managers remain internal.

### Access Ownership and Ordering

- Hardware will not detect data hazards between the DMA, Gemmini and CPUs. Software owns buffer synchronization.
- A region must not be read while the DMA is filling it or modified while the DMA or Gemmini is consuming it. Ping-pong buffers are the intended execution model.
- Gemmini accesses the globally mapped scratchpad address `spadBase + localOffset` through its existing DMA. ActiveSPM DMA commands use only `localOffset` for the local side.
- Software will use RISC-V memory fences to order source-buffer writes, descriptor writes, `START`, completion polling and subsequent destination use.
- Because the ActiveSPM DMA is a coherent SBus client, normal transfers to cacheable memory do not require a software-managed L2 flush. A fence orders accesses but is not a substitute for a cache flush on any future non-coherent path.

### TileLink and System Bus Connection

- ActiveSPM has three external TileLink interfaces: an MMIO control manager, an aggregated scratchpad data manager and an external-memory DMA client.
- The DMA memory-side client and both scratchpad access paths will issue or accept only `Get`, `PutFullData` and `PutPartialData`. Acquire, atomic and hint operations are not supported.
- The scratchpad data manager will attach to SBus with `coupleTo`. The DMA client will attach to SBus with `coupleFrom`.
- The MMIO control manager will attach to CBus and be reachable from RocketCore through the normal SBus control path. It does not require a dedicated SBus NoC endpoint.
- The DMA client will attach to coherent SBus. DRAM requests will pass through the configured coherence manager and MBus.
- An inclusive L2 cache or a broadcast coherence manager may be used. A broadcast manager is preferred when no L2 data cache is wanted; the coherence manager must remain present in the intended multi-core and multi-master system.
- Configured external-memory address ranges will prevent accidental DMA access to unrelated MMIO regions. This check prevents erroneous access but is not a security boundary.
- TileLink widths and transfer sizes will be negotiated through Diplomacy. The initial DMA will use only single-beat or smaller operations and split requests according to negotiated capabilities, alignment and address boundaries.
- Standard adapters such as `TLBuffer`, `TLFragmenter` and `TLWidthWidget` may be inserted only as required at defined attachment boundaries. The byte realigner belongs to the DMA datapath and must not depend on matching external and local beat alignment.
- Each single-outstanding interface will use an explicit source ID and will not depend on FIFO response ordering from the NoC.

### Clock and Reset

- The first implementation will place the DMA, MMIO registers, internal TileLink path and all scratchpad banks in one ActiveSPM clock domain, normally the SBus/system clock domain.
- Independent ActiveSPM clocks and dynamic clocking are not supported initially.
- If a platform configures CBus and SBus with different clocks, the subsystem attachment boundary must provide the standard TileLink crossing. ActiveSPM will not implement an implicit internal clock-domain crossing.

### NoC Mapping

- The initial NoC integration will use Constellation `SimpleTLNoCParams` with complete TileLink channels. Split-channel and shared global NoC configurations are later optimizations.
- The external node names of instance `i` are `activespm-ctrl[i]`, `activespm-spad[i]` and `activespm-dma[i]` for its MMIO control manager, scratchpad data manager and DMA client respectively.
- These strings must be the actual Diplomacy client or manager parameter names, not only Scala variable names or generated RTL names.
- `activespm-dma[i]` is an SBus NoC ingress and `activespm-spad[i]` is an SBus NoC egress. They should map to the same router when they represent one physical memory tile.
- `activespm-ctrl[i]` does not require a separate SBus NoC mapping because it is attached to CBus.
- Individual banks and the DMA local client have no external NoC node names.
- The bracketed instance ID is part of each Diplomacy name and Constellation mapping key. It prevents instance `1` and instance `10` from matching one another under substring matching.

### Initial Scope and Verification

- The reference implementation will use polling, one active command, at most one outstanding transaction per DMA interface, bounded buffering, one clock domain and no interrupt, queue, ECC or hardware ownership tracking.
- Hardware tests will cover the DMA and scratchpad separately before testing integrated and multi-instance systems.
- Tests will include both directions, zero and arbitrary byte counts, representative combinations of source and destination byte alignment, partial writes, bank boundaries, range and overflow errors, TileLink errors, backpressure, reset during transfer and a second `START` while busy.
- Integration tests will cover Gemmini `mvin` from ActiveSPM, Gemmini `mvout` to ActiveSPM, ActiveSPM transfers to and from memory, concurrent non-overlapping traffic and exact NoC endpoint-name matching.

## Development Progress

Add a short summary of progress here after each dev round.

- Timestamp: Summary of progress.
- Timestamp: Summary of progress.
- Timestamp: Summary of progress.
- 2026-09-13: Created the ActiveSPM submodule and empty generator directory skeleton, established matching `main` and `npu/dev` branches, and connected the empty project to the top-level Chipyard build.
- 2026-09-13: Added the elaboratable ActiveSPM Scala framework, stable control/DMA interface contracts, TileLink node shells, multi-instance subsystem attachment, scaffold configuration, tests, and user-facing documentation.
- 2026-09-13: Replaced the fail-fast scratchpad shell with shared banked `TLRAM` storage, added native-width/SBus adaptation and a wide-SBus scaffold, and verified full, partial, concurrent, backpressured, and reset-time accesses across the global and DMA-local TileLink paths.
- 2026-09-13: Implemented the bidirectional byte-accurate DMA datapath with negotiated-width realignment, single-outstanding read/write pipelining, descriptor validation, TileLink error draining, completion progress, and direct hardware-interface tests; MMIO remains a reserved shell.
