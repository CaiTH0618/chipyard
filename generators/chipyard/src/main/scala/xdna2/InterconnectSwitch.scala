package chipyard.xdna2

import chisel3._
import chisel3.experimental.UnlocatableSourceInfo
import chisel3.util.MixedVec
import chipyard.Xdna2ArrayParams
import constellation.channel._
import constellation.noc.{InternalNoCKey, InternalNoCParams}
import constellation.protocol.{TileLinkABCDEProtocolParams, TLConnect, TLMasterToNoC, TLSlaveToNoC}
import constellation.router.Router
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.tilelink._
import org.chipsalliance.cde.config.{Field, Parameters}

case class Xdna2SwitchParams(x: Int = 3, y: Int = 2) {
  require(x >= 0 && x < 8 && y >= 0 && y < 5, "Switch location must be inside the 8x5 mesh")
  val nodeId = y * 8 + x
}
case object Xdna2SwitchKey extends Field[Xdna2SwitchParams](Xdna2SwitchParams())

/** Full-network protocol metadata only; this does not instantiate the other tiles/routers.
  * Two host cache ports and a serial/control port retain the coherent traffic classes
  * present in the full integration model, without adding their hardware to the switch.
  */
private[xdna2] class AreaNetwork(implicit p: Parameters) {
  val array = Xdna2ArrayParams()
  val masters = AreaTileLink.networkMasters(array)
  val masterNodes = Seq.tabulate(array.computeTiles)(i => array.columns + i) ++
    (0 until array.columns) ++ Seq.tabulate(array.controlCores)(array.hostRouter) ++ Seq(0)
  val slaves = array.memTiles.map(m => AreaTileLink.memory(Seq(m.scratchpadAddress))) ++ Seq(
    AreaTileLink.memory(Seq(AreaTileLink.dram), coherent = true),
    AreaTileLink.memory(Seq(AddressSet(0, 0x1fffffffL))))
  val slaveNodes = (0 until array.columns) ++ Seq(array.columns - 1, 0)

  private val mergedMaster = AreaTileLink.mergedMasters(masters)
  private val mergedSlave = slaves.head.v1copy(
    managers = slaves.flatMap(_.managers),
    endSinkId = TLXbar.mapOutputIds(slaves).map(_.end).max)
  val edgesIn = masters.map(m => new TLEdgeIn(m, mergedSlave, p, UnlocatableSourceInfo))
  val edgesOut = slaves.map(m => new TLEdgeOut(mergedMaster, m, p, UnlocatableSourceInfo))
  val protocol = TileLinkABCDEProtocolParams(edgesIn, edgesOut, masterNodes, slaveNodes)
  val payloadBits = protocol.minPayloadWidth
  require(payloadBits == array.networkDataBits + array.networkBeatBytes + 1,
    "The XDNA 2 NoC must carry one network data beat, byte mask and corrupt bit")
  val noc = array.dataMesh.nocParams.copy(
    nocName = "xdna2-area",
    ingresses = protocol.ingressNodes.map(n => UserIngressParams(n, payloadBits)),
    egresses = protocol.egressNodes.map(n => UserEgressParams(n, payloadBits)),
    routerParams = _ => constellation.router.UserRouterParams(payloadBits = payloadBits),
    vNetBlocking = protocol.vNetBlocking,
    flows = protocol.flows)
  val internal = InternalNoCParams(noc)
}

/** A mesh position consists of its base router, terminal router and local TL interfaces.
  * All neighboring links are exposed, including credit return and VC allocation state.
  */
class InterconnectSwitch(implicit p: Parameters) extends LazyModule {
  private val network = new AreaNetwork
  private val internal = network.internal
  private val position = p(Xdna2SwitchKey).nodeId
  private val selected = Seq(position, position + 40)
  private val ip = p.alterPartial { case InternalNoCKey => internal }

  private val routers = selected.map { node =>
    val router = LazyModule(new Router(
      internal.routerParams(node),
      internal.channelParams.filter(_.destId == node),
      internal.ingressParams.filter(_.destId == node),
      internal.channelParams.filter(_.srcId == node).map(_.destId),
      internal.egressParams.filter(_.srcId == node).map(_.egressId))(ip))
    router.suggestName(if (node == position) "terminal_router" else "mesh_router")
    node -> router
  }.toMap

  private def source(cp: ChannelParams) = routers(cp.srcId).sourceNodes.find(_.destId == cp.destId).get
  private def dest(cp: ChannelParams) = routers(cp.destId).destNodes.find(_.destParams.srcId == cp.srcId).get

  // Diplomacy captures Parameters at each binding, separately from the router's
  // constructor parameters. Flit bundles need InternalNoCKey on both ends.
  private def connect(sink: ChannelDestNode, source: ChannelSourceNode): Unit = {
    implicit val p: Parameters = ip
    sink := source
  }
  private def connectIngress(sink: IngressChannelDestNode, source: IngressChannelSourceNode): Unit = {
    implicit val p: Parameters = ip
    sink := source
  }
  private def connectEgress(sink: EgressChannelDestNode, source: EgressChannelSourceNode): Unit = {
    implicit val p: Parameters = ip
    sink := source
  }

  internal.channelParams.filter(c => selected.contains(c.srcId) && selected.contains(c.destId))
    .foreach(c => connect(dest(c), source(c)))

  private val inputs = internal.channelParams.filter(c => !selected.contains(c.srcId) && selected.contains(c.destId))
    .map { c =>
      val boundary = ChannelSourceNode(c.destId)
      connect(dest(c), boundary)
      boundary
    }
  private val outputs = internal.channelParams.filter(c => selected.contains(c.srcId) && !selected.contains(c.destId))
    .map { c =>
      val boundary = ChannelDestNode(c)
      connect(boundary, source(c))
      boundary
    }

  private val ingress = internal.ingressParams.filter(_.destId == position).map { c =>
    val boundary = IngressChannelSourceNode(c.destId)
    connectIngress(routers(position).ingressNodes.find(_.destParams.ingressId == c.ingressId).get, boundary)
    c.ingressId -> boundary
  }.toMap
  private val egress = internal.egressParams.filter(_.srcId == position).map { c =>
    val boundary = EgressChannelDestNode(c)
    connectEgress(boundary, routers(position).egressNodes.find(_.egressId == c.egressId).get)
    c.egressId -> boundary
  }.toMap
  private val debug = selected.map { n =>
    val sink = BundleBridgeSink[constellation.router.DebugBundle]()
    sink := routers(n).debugNode
    sink
  }
  private val localMasters = network.masterNodes.indices.filter(network.masterNodes(_) == position)
  private val localSlaves = network.slaveNodes.indices.filter(network.slaveNodes(_) == position)

  lazy val module = new Impl
  class Impl extends LazyModuleImp(this) {
    val io = IO(new Bundle {
      val in = MixedVec(inputs.map(n => Flipped(chiselTypeOf(n.out.head._1))))
      val out = MixedVec(outputs.map(n => chiselTypeOf(n.in.head._1)))
      val masters = MixedVec(localMasters.map(i => Flipped(new TLBundle(network.edgesIn(i).bundle))))
      val slaves = MixedVec(localSlaves.map(i => new TLBundle(network.edgesOut(i).bundle)))
    })
    inputs.zip(io.in).foreach { case (node, port) => node.out.head._1 <> port }
    outputs.zip(io.out).foreach { case (node, port) => port <> node.in.head._1 }

    localMasters.zip(io.masters).foreach { case (i, port) =>
      val range = network.protocol.inputIdRanges(i)
      val nif = Module(new TLMasterToNoC(
        network.edgesIn(i), network.edgesOut, range.start, range.size, network.protocol.wideBundle,
        s => s * 3 + network.masters.size * 2, network.payloadBits)(ip))
      nif.io.tilelink := DontCare
      nif.io.tilelink.a.valid := false.B
      nif.io.tilelink.c.valid := false.B
      nif.io.tilelink.e.valid := false.B
      TLConnect(nif.io.tilelink.a, port.a)
      TLConnect(port.d, nif.io.tilelink.d)
      if (port.params.hasBCE) {
        TLConnect(port.b, nif.io.tilelink.b)
        TLConnect(nif.io.tilelink.c, port.c)
        TLConnect(nif.io.tilelink.e, port.e)
      }
      ingress(i * 3).out.head._1.flit <> nif.io.flits.a
      ingress(i * 3 + 1).out.head._1.flit <> nif.io.flits.c
      ingress(i * 3 + 2).out.head._1.flit <> nif.io.flits.e
      nif.io.flits.b <> egress(i * 2).in.head._1.flit
      nif.io.flits.d <> egress(i * 2 + 1).in.head._1.flit
    }

    localSlaves.zip(io.slaves).foreach { case (i, port) =>
      val range = network.protocol.outputIdRanges(i)
      val nif = Module(new TLSlaveToNoC(
        network.edgesOut(i), network.edgesIn, range.start, range.size, network.protocol.wideBundle,
        s => s * 2, network.payloadBits)(ip))
      nif.io.tilelink := DontCare
      nif.io.tilelink.b.valid := false.B
      nif.io.tilelink.d.valid := false.B
      TLConnect(port.a, nif.io.tilelink.a)
      TLConnect(nif.io.tilelink.d, port.d)
      if (port.params.hasBCE) {
        TLConnect(nif.io.tilelink.b, port.b)
        TLConnect(port.c, nif.io.tilelink.c)
        TLConnect(port.e, nif.io.tilelink.e)
      }
      ingress(i * 2 + network.masters.size * 3).out.head._1.flit <> nif.io.flits.b
      ingress(i * 2 + 1 + network.masters.size * 3).out.head._1.flit <> nif.io.flits.d
      nif.io.flits.a <> egress(i * 3 + network.masters.size * 2).in.head._1.flit
      nif.io.flits.c <> egress(i * 3 + 1 + network.masters.size * 2).in.head._1.flit
      nif.io.flits.e <> egress(i * 3 + 2 + network.masters.size * 2).in.head._1.flit
    }

    println(s"XDNA 2 switch at node $position: ${inputs.size} mesh inputs, ${outputs.size} mesh outputs, " +
      s"${network.payloadBits}-bit payload, ${localMasters.size} local masters, ${localSlaves.size} local slaves")
  }
}
