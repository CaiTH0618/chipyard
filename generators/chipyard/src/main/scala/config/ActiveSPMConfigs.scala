package chipyard

import activespm.{ActiveSPMParams, WithActiveSPM}
import constellation.channel.{UserChannelParams, UserVirtualChannelParams}
import constellation.noc.NoCParams
import constellation.protocol.{DiplomaticNetworkNodeMapping, SimpleTLNoCParams}
import constellation.routing.{BlockingVirtualSubnetworksRouting, Mesh2DEscapeRouting, TerminalRouterRouting}
import constellation.topology.{Mesh2D, TerminalRouter}
import freechips.rocketchip.diplomacy.AddressSet
import freechips.rocketchip.prci.NoCrossing
import org.chipsalliance.cde.config.Config

import scala.collection.immutable.ListMap

/** Single-instance configuration for ActiveSPM subsystem integration checks. */
class ActiveSPMScaffoldRocketConfig extends Config(
  new WithActiveSPM(Seq(ActiveSPMParams(
    id = 0,
    controlAddress = AddressSet(0x10050000L, 0xfffL),
    scratchpadAddress = AddressSet(0x70000000L, 0xffffL),
    spadBeatBytes = 8,
    nBanks = 4,
    externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
    controlXType = NoCrossing))) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)

/** Elaboration-only scaffold with a 16-byte SBus and an 8-byte scratchpad. */
class ActiveSPMWideSBusScaffoldRocketConfig extends Config(
  new chipyard.config.WithSystemBusWidth(128) ++
  new ActiveSPMScaffoldRocketConfig)

/**
  * Dual Rocket/Gemmini/ActiveSPM integration configuration.
  *
  * The 3x2 SBus mesh is laid out by row as
  * `Core+Gemmini -> ActiveSPM -> L2 bank`. The DMA ingress and aggregated
  * scratchpad egress of each ActiveSPM share that instance's dedicated router;
  * its control manager remains attached through CBus.
  */
class ActiveSPMDualGemminiMeshRocketConfig extends Config(
  new constellation.soc.WithSbusNoC(SimpleTLNoCParams(
    DiplomaticNetworkNodeMapping(
      inNodeMapping = ListMap(
        "serial_tl" -> 0,
        "Core 0" -> 0,
        "Gemmini[0]" -> 0,
        "activespm-dma[0]" -> 1,
        "Core 1" -> 3,
        "Gemmini[1]" -> 3,
        "activespm-dma[1]" -> 4),
      outNodeMapping = ListMap(
        "pbus" -> 0,
        "activespm-spad[0]" -> 1,
        "system[0]" -> 2,
        "activespm-spad[1]" -> 4,
        "system[1]" -> 5)),
    NoCParams(
      topology = TerminalRouter(Mesh2D(3, 2)),
      channelParamGen = (_, _) => UserChannelParams(
        Seq.fill(5)(UserVirtualChannelParams(4))),
      routingRelation = BlockingVirtualSubnetworksRouting(
        TerminalRouterRouting(Mesh2DEscapeRouting()), 5, 1)))) ++
  new WithActiveSPM(Seq(
    ActiveSPMParams(
      id = 0,
      controlAddress = AddressSet(0x10050000L, 0xfffL),
      scratchpadAddress = AddressSet(0x70000000L, 0xffffL),
      spadBeatBytes = 8,
      nBanks = 4,
      externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
      controlXType = NoCrossing),
    ActiveSPMParams(
      id = 1,
      controlAddress = AddressSet(0x10051000L, 0xfffL),
      scratchpadAddress = AddressSet(0x70010000L, 0xffffL),
      spadBeatBytes = 8,
      nBanks = 4,
      externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
      controlXType = NoCrossing))) ++
  new chipyard.config.WithMultiRoCCGemmini(0, 1)(gemmini.GemminiConfigs.defaultConfig) ++
  new chipyard.config.WithMultiRoCC ++
  new freechips.rocketchip.rocket.WithNHugeCores(2) ++
  new freechips.rocketchip.subsystem.WithNBanks(2) ++
  new freechips.rocketchip.subsystem.WithInclusiveCache() ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)
