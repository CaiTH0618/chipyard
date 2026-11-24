package chipyard.xdna2

import activespm.ActiveSPM
import chisel3._
import chipyard.Xdna2ArrayParams
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.tile.TileVisibilityNodeKey
import freechips.rocketchip.tilelink._
import freechips.rocketchip.util.ElaborationArtefacts
import gemmini.Arithmetic.SIntArithmetic
import gemmini.Gemmini
import org.chipsalliance.cde.config.{Field, Parameters}

case object Xdna2AreaModuleKey extends Field[String]("aie")
case object Xdna2DeSCQueueDepth extends Field[Int](0)

/** External endpoints describe protocol capabilities; they contain no RAM or traffic generator. */
private[xdna2] object AreaTileLink {
  val beatBytes = Xdna2ArrayParams().networkBeatBytes
  val localBeatBytes = Xdna2ArrayParams().localScratchpadBeatBytes
  val blockBytes = 64
  val dram = AddressSet(0x80000000L, 0x0fffffffL)

  def client(name: String, sources: Int = 16) = TLMasterPortParameters.v1(Seq(
    TLMasterParameters.v1(name = name, sourceId = IdRange(0, sources))))

  def networkMasters(array: Xdna2ArrayParams): Seq[TLMasterPortParameters] = {
    val compute = Seq.tabulate(array.computeTiles)(i => client(s"aie-$i", 32))
    val mem = Seq.tabulate(array.columns)(i => client(s"mem-dma-$i", array.memDmaMaxInFlight))
    val hosts = Seq.tabulate(array.controlCores)(i => TLMasterPortParameters.v1(Seq(
      TLMasterParameters.v1(name = s"host-$i", sourceId = IdRange(0, 16),
        supportsProbe = TransferSizes(64, 64)))))
    compute ++ mem ++ hosts ++ Seq(client("serial-control"))
  }

  def mergedMasters(ports: Seq[TLMasterPortParameters]) = ports.head.v1copy(
    clients = ports.zip(TLXbar.mapInputIds(ports)).flatMap { case (port, range) =>
      port.clients.map(c => c.v1copy(sourceId = c.sourceId.shift(range.start)))
    })

  def memory(address: Seq[AddressSet], coherent: Boolean = false) = TLSlavePortParameters.v1(
    Seq(TLSlaveParameters.v1(
      address = address,
      regionType = if (coherent) RegionType.CACHED else RegionType.UNCACHED,
      executable = true,
      supportsGet = TransferSizes(1, blockBytes),
      supportsPutFull = TransferSizes(1, blockBytes),
      supportsPutPartial = TransferSizes(1, blockBytes),
      supportsAcquireB = if (coherent) TransferSizes(blockBytes, blockBytes) else TransferSizes.none,
      supportsAcquireT = if (coherent) TransferSizes(blockBytes, blockBytes) else TransferSizes.none)),
    beatBytes = beatBytes,
    endSinkId = if (coherent) 4 else 0)
}

/** One 32x32 INT8 Gemmini, including DMA and 64+64 KiB of SRAM, plus a
  * separate 32-lane FP32 SIMD, a vector register file and shared scratchpad.
  * RoCC commands, responses and PTW remain external, so no host/cache is needed.
  */
class AIETile(implicit p: Parameters) extends LazyModule {
  private val params = Xdna2ArrayParams()
  private val visibility = TLEphemeralNode()
  private val gp = p.alterPartial { case TileVisibilityNodeKey => visibility }
  private val config = params.gemminiConfig(0).copy(use_shared_ext_mem = true)
  private val gemmini = LazyModule(new Gemmini(config)(SIntArithmetic, gp))
  private val memory = TLManagerNode(Seq(AreaTileLink.memory(Seq(AddressSet(0, 0xffffffffL)))))
  memory := visibility := gemmini.tlNode

  lazy val module = new Impl
  class Impl extends LazyModuleImp(this) {
    private val simd = Module(new FP32SimdEngine(params.fp32SimdLanes, params.fp32VectorRegisters))
    private val sharedMemory = Module(new AIETileSharedMemory(
      config.sp_banks, config.sp_bank_entries, params.arrayDimension * 8,
      config.acc_banks, config.acc_sub_banks, config.acc_bank_entries / config.acc_sub_banks,
      params.arrayDimension * 32))
    val io = IO(new Bundle {
      val rocc = chiselTypeOf(gemmini.module.io)
      val dma = new TLBundle(memory.in.head._2.bundle)
      val simd = new FP32SimdControlIO(params.fp32VectorRegisters)
    })
    io.rocc <> gemmini.module.io
    io.dma <> memory.in.head._1
    io.simd <> simd.io.control
    sharedMemory.io.gemmini <> gemmini.module.ext_mem_io.get
    sharedMemory.io.simd <> simd.io.scratchpad
  }
}

/** One 512-KiB ActiveSPM, including its DMA, registers and local bank crossbar.
  * Width conversion and fragmentation accept 64-byte network beats/requests
  * at the native 32-byte SRAM port, without widening the physical SRAM macros.
  */
class MemTile(implicit p: Parameters) extends LazyModule {
  private val active = LazyModule(new ActiveSPM(
    Xdna2ArrayParams().memTiles.head.copy(descQueueDepth = p(Xdna2DeSCQueueDepth))))
  private val control = TLClientNode(Seq(AreaTileLink.client("memtile-control")))
  private val networkClients = AreaTileLink.mergedMasters(AreaTileLink.networkMasters(Xdna2ArrayParams()))
  // The banked SRAM is uncached. Preserve the network source-ID range, but
  // bound local concurrency before the fragmenter expands source IDs.
  private val scratchpad = TLClientNode(Seq(AreaTileLink.client("memtile-scratchpad", networkClients.endSourceId)))
  private val memory = TLManagerNode(Seq(AreaTileLink.memory(Seq(AreaTileLink.dram))))

  active.controlNode := control
  active.scratchpadNode := TLFIFOFixer() :=
    TLFragmenter(AreaTileLink.localBeatBytes, AreaTileLink.blockBytes) :=
    TLWidthWidget(AreaTileLink.beatBytes) := TLSourceShrinker(32) := scratchpad
  memory := active.dmaNode

  lazy val module = new Impl
  class Impl extends LazyModuleImp(this) {
    val io = IO(new Bundle {
      val control = Flipped(new TLBundle(MemTile.this.control.out.head._2.bundle))
      val scratchpad = Flipped(new TLBundle(MemTile.this.scratchpad.out.head._2.bundle))
      val dma = new TLBundle(memory.in.head._2.bundle)
      val desc = active.module.desc.map(chiselTypeOf(_))
    })
    MemTile.this.control.out.head._1 <> io.control
    MemTile.this.scratchpad.out.head._1 <> io.scratchpad
    io.dma <> memory.in.head._1
    io.desc.zip(active.module.desc).foreach { case (port, inner) => inner <> port }
  }
}

/** Transparent generation shell. Every functional port is passed to top-level IO. */
class Xdna2AreaHarness(implicit p: Parameters) extends Module {
  private val profile = Xdna2ArrayParams()
  ElaborationArtefacts.add("xdna2-profile.json", s"""{
    "data_bits": ${profile.networkDataBits},
    "beat_bytes": ${profile.networkBeatBytes},
    "payload_bits": ${profile.networkDataBits + profile.networkBeatBytes + 1},
    "virtual_channels": ${profile.networkVirtualChannels},
    "buffer_depth": ${profile.networkBufferDepth},
    "local_scratchpad_bits": ${profile.localScratchpadBeatBytes * 8},
    "mem_dma_in_flight": ${profile.memDmaMaxInFlight},
    "mem_dma_buffer_bytes": ${profile.memDmaBufferBytes},
    "mem_dma_local_ports": ${profile.memDmaLocalPorts},
    "mem_dma_local_in_flight": ${profile.memDmaLocalInFlight},
    "desc_queue_depth": ${p(Xdna2DeSCQueueDepth)},
    "output_queues": true,
    "mesh_columns": ${profile.columns},
    "mesh_rows": ${profile.computeRows + 1}
  }""")
  private def expose[T <: Data](ports: T): Unit = {
    val io = IO(chiselTypeOf(ports)).suggestName("io")
    io <> ports
  }

  p(Xdna2AreaModuleKey) match {
    case "aie" => expose(Module(LazyModule(new AIETile).module).io)
    case "mem" => expose(Module(LazyModule(new MemTile).module).io)
    case "switch" => expose(Module(LazyModule(new InterconnectSwitch).module).io)
    case other => throw new IllegalArgumentException(s"Unknown XDNA 2 area module: $other")
  }
}
