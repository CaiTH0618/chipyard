package chipyard

import activespm.{ActiveSPMParams, WithActiveSPM}
import chisel3._
import constellation.channel.{UserChannelParams, UserVirtualChannelParams}
import constellation.noc.NoCParams
import constellation.protocol.{DiplomaticNetworkNodeMapping, SimpleTLNoCParams}
import constellation.routing.{BlockingVirtualSubnetworksRouting, Mesh2DEscapeRouting, TerminalRouterRouting}
import constellation.topology.{Mesh2D, TerminalRouter}
import freechips.rocketchip.diplomacy.{AddressSet, LazyModule}
import freechips.rocketchip.prci.NoCrossing
import freechips.rocketchip.rocket.DCacheParams
import freechips.rocketchip.subsystem.{CBUS, CacheBlockBytes, ControlBusKey, InSubsystem, SBUS, SystemBusKey, TLNetworkTopologyLocated}
import freechips.rocketchip.tile.BuildRoCC
import freechips.rocketchip.tilelink.{TLBusWrapperConnection, TLBusWrapperTopology, TLFIFOFixer, TLFragmenter, TLWidthWidget}
import gemmini.Arithmetic.SIntArithmetic
import gemmini.{CapacityInKilobytes, Dataflow, Float, Gemmini, GemminiConfigs}
import org.chipsalliance.cde.config.{Config, Parameters}
import rerocc.client.ReRoCCClientParams
import rerocc.manager.ReRoCCTileParams

import scala.collection.immutable.ListMap

/** Resource model of an XDNA 2 column, rather than an implementation of the AIE ISA.
  *
  * Each logical compute tile contains one 32x32 INT8 Gemmini (1,024 MACs/cycle).
  * Its 128-KiB SRAM budget includes a 64-KiB scratchpad and a 64-KiB INT32
  * accumulator. Each memory tile contains 512 KiB of SRAM.
  * The full eight-column array has 32,768 MACs/cycle and 8 MiB of tile SRAM,
  * including the accumulators. Host caches are outside that SRAM total.
  */
case class Xdna2ArrayParams(columns: Int = 8) {
  require(columns >= 1 && columns <= 8, "XDNA 2 proxy supports one through eight columns")

  val computeRows = 4
  val enginesPerTile = 1
  val arrayDimension = 32
  // Standalone AIETile adds this execution unit beside its INT8 Gemmini.
  val fp32SimdLanes = 32
  val fp32VectorRegisters = 8
  val scratchpadKiB = 64
  val accumulatorKiB = 64
  val computeTiles = columns * computeRows
  val gemminiEngines = computeTiles * enginesPerTile
  val controlCores = (gemminiEngines + 15) / 16
  val memTileBytes = 512 * 1024
  val networkDataBits = 512
  val networkBeatBytes = networkDataBits / 8
  val localScratchpadBeatBytes = 32
  val memDmaMaxInFlight = 64
  val memDmaBufferBytes = 4096
  val memDmaLocalPorts = 2
  val memDmaLocalInFlight = 16
  // The ABCDE routing assigns escape resources to five protocol classes.
  val networkVirtualChannels = 5
  val networkBufferDepth = 2

  def computeRouter(engineId: Int): Int = columns + engineId / enginesPerTile
  def hostRouter(hartId: Int): Int = hartId * columns / controlCores

  def gemminiConfig(engineId: Int) = GemminiConfigs.defaultConfig.copy[SInt, Float, Float](
    gemmini_id = engineId,
    inputType = SInt(8.W),
    spatialArrayOutputType = SInt(32.W),
    accType = SInt(32.W),
    tileRows = 1,
    tileColumns = 1,
    meshRows = arrayDimension,
    meshColumns = arrayDimension,
    dataflow = Dataflow.WS,
    sp_capacity = CapacityInKilobytes(scratchpadKiB),
    sp_banks = 4,
    sp_singleported = true,
    acc_capacity = CapacityInKilobytes(accumulatorKiB),
    acc_banks = 2,
    acc_singleported = true,
    acc_sub_banks = 2,
    dma_buswidth = localScratchpadBeatBytes * 8,
    dma_maxbytes = 64,
    max_in_flight_mem_reqs = 16,
    mvin_scale_args = None,
    mvin_scale_acc_args = None,
    mvin_scale_shared = false,
    acc_scale_args = None,
    has_training_convs = false,
    has_max_pool = false,
    has_nonlinear_activations = false,
    has_dw_convs = false,
    has_normalizations = false,
    has_first_layer_optimizations = false,
    ex_read_from_acc = false,
    ex_write_to_spad = false,
    hardcode_d_to_garbage_addr = true,
    use_shared_ext_mem = false,
    shared_scratchpad_config = GemminiConfigs.defaultConfig.shared_scratchpad_config.copy(enable = false),
    num_counter = 4)

  def memTiles: Seq[ActiveSPMParams] = Seq.tabulate(columns) { column =>
    ActiveSPMParams(
      id = column,
      controlAddress = AddressSet(0x10050000L + column * 0x1000L, 0xfffL),
      scratchpadAddress = AddressSet(0x70000000L + column * memTileBytes.toLong, memTileBytes - 1L),
      spadBeatBytes = localScratchpadBeatBytes,
      nBanks = 8,
      externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
      controlXType = NoCrossing,
      dmaMaxInFlight = memDmaMaxInFlight,
      dmaBufferBytes = memDmaBufferBytes,
      dmaLocalPorts = memDmaLocalPorts,
      dmaLocalInFlight = memDmaLocalInFlight,
      globalPipelineDepth = 8,
      bankResponseQueueDepth = 2)
  }

  def dataMesh: SimpleTLNoCParams = {
    // ReRoCC merges each Gemmini DMA and its manager's cache/PTW path into one
    // SBus ingress. Match the unique Gemmini name in that combined name.
    val ingress = Seq("serial_tl" -> 0) ++
      Seq.tabulate(controlCores)(hart => s"Core $hart" -> hostRouter(hart)) ++
      Seq.tabulate(gemminiEngines)(engine => s"Gemmini[$engine]" -> computeRouter(engine)) ++
      Seq.tabulate(columns)(column => s"activespm-dma[$column]" -> column)
    val egress = Seq("pbus" -> 0, "system[0]" -> (columns - 1)) ++
      Seq.tabulate(columns)(column => s"activespm-spad[$column]" -> column)

    SimpleTLNoCParams(
      DiplomaticNetworkNodeMapping(
        inNodeMapping = ListMap(ingress: _*),
        outNodeMapping = ListMap(egress: _*)),
      NoCParams(
        topology = TerminalRouter(Mesh2D(columns, computeRows + 1)),
        channelParamGen = (_, _) => UserChannelParams(
          Seq.fill(networkVirtualChannels)(UserVirtualChannelParams(networkBufferDepth))),
        routingRelation = BlockingVirtualSubnetworksRouting(
          TerminalRouterRouting(Mesh2DEscapeRouting()), networkVirtualChannels, 1)))
  }
}

/** BuildRoCC is consumed by WithReRoCC on the left of this fragment. */
class WithXdna2GemminiEngines(params: Xdna2ArrayParams) extends Config((site, here, up) => {
  case BuildRoCC => up(BuildRoCC) ++ Seq.tabulate(params.gemminiEngines) { engine =>
    (p: Parameters) => {
      implicit val q: Parameters = p
      LazyModule(new Gemmini(params.gemminiConfig(engine)))
    }
  }
})

/** ReRoCC's control registers accept eight-byte accesses. Adapt block-sized
  * requests at the control-bus boundary so Gemmini's DMA TLB can describe
  * every reachable manager. The FIFO fixer orders responses across devices
  * before they are reassembled by the fragmenter.
  */
class WithXdna2ControlBusFragmentation extends Config((site, here, up) => {
  case TLNetworkTopologyLocated(InSubsystem) =>
    up(TLNetworkTopologyLocated(InSubsystem)).map {
      case topology: TLBusWrapperTopology =>
        new TLBusWrapperTopology(topology.instantiations, topology.connections.map {
          case (SBUS, CBUS, connection: TLBusWrapperConnection) =>
            (SBUS, CBUS, TLBusWrapperConnection(
              connection.xType,
              connection.driveClockFromMaster,
              connection.nodeBinding,
              connection.flipRendering)(
              slaveNodeView = { case (bus, p) => bus.crossInHelper(connection.xType)(p) },
              inject = p => {
                implicit val q: Parameters = p
                TLFIFOFixer(TLFIFOFixer.all) :*=
                  TLWidthWidget(p(ControlBusKey).beatBytes) :*=
                  TLFragmenter(p(ControlBusKey).beatBytes, p(CacheBlockBytes), holdFirstDeny = true) :*=
                  TLWidthWidget(p(SystemBusKey).beatBytes)
              }))
          case other => other
        })
      case other => other
    }
})

/** Common construction for the full array and its single-column elaboration scaffold.
  *
  * Host cores, the 64-KiB host LLC, and the ReRoCC command crossbar are
  * integration infrastructure, not additional AIE compute or memory tiles.
  * Each ReRoCC manager also has a 256-byte page-table cache (8 KiB plus
  * tags for the full array). The cacheless HellaMMIO path in the current
  * ReRoCC revision leaves HellaCacheIO.store_pending uninitialized.
  * Program memories, the AIE ISA, BFP16, and AMD stream/cascade networks are
  * not modeled. ActiveSPM retains its single-command MMIO contract, with
  * concurrent transactions and two private SRAM access lanes internally.
  */
class Xdna2ArrayConfig(params: Xdna2ArrayParams) extends Config(
  // Protocol verification monitors are not part of the area model.
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++
  new WithXdna2ControlBusFragmentation ++
  new constellation.soc.WithSbusNoC(params.dataMesh) ++
  new WithActiveSPM(params.memTiles) ++
  new rerocc.WithReRoCC(
    clientParams = ReRoCCClientParams(nCfgs = 16),
    reRoCCManagerParams = ReRoCCTileParams(dcacheParams = Some(DCacheParams(
      nSets = 4,
      nWays = 1,
      nMSHRs = 0,
      nTLBSets = 1,
      nTLBWays = 4)))) ++
  new WithXdna2GemminiEngines(params) ++
  new freechips.rocketchip.rocket.WithNMedCores(params.controlCores) ++
  new testchipip.soc.WithNoScratchpads ++
  new freechips.rocketchip.subsystem.WithNBanks(1) ++
  new freechips.rocketchip.subsystem.WithInclusiveCache(nWays = 4, capacityKB = 64) ++
  new chipyard.config.WithSystemBusWidth(params.networkDataBits) ++
  new chipyard.config.AbstractConfig)

/** Full 8x4 compute array plus eight 512-KiB memory tiles on an 8x5 data mesh. */
class Xdna2Config extends Xdna2ArrayConfig(Xdna2ArrayParams())

/** Four logical compute tiles, four Gemminis, and one 512-KiB memory tile. */
class Xdna2ColumnConfig extends Xdna2ArrayConfig(Xdna2ArrayParams(columns = 1))
