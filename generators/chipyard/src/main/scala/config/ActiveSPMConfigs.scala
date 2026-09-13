package chipyard

import activespm.{ActiveSPMParams, WithActiveSPM}
import freechips.rocketchip.diplomacy.AddressSet
import freechips.rocketchip.prci.NoCrossing
import org.chipsalliance.cde.config.Config

/** Elaboration-only configuration for the non-functional ActiveSPM scaffold. */
class ActiveSPMScaffoldRocketConfig extends Config(
  new WithActiveSPM(Seq(ActiveSPMParams(
    id = 0,
    controlAddress = AddressSet(0x10050000L, 0xfffL),
    scratchpadAddress = AddressSet(0x70000000L, 0xffffL),
    beatBytes = 8,
    nBanks = 4,
    externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
    controlXType = NoCrossing))) ++
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new chipyard.config.AbstractConfig)
