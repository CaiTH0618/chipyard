package chipyard

import activespm.{ActiveSPMParams, WithActiveSPM}
import freechips.rocketchip.diplomacy.AddressSet
import freechips.rocketchip.prci.NoCrossing
import freechips.rocketchip.subsystem.{WithInclusiveCache, WithNBanks}
import org.chipsalliance.cde.config.Config

// Single-group baseline: 2 MiB shared L2, no ActiveSPM.
class VCU128DualRocketDualGemminiResNet18CacheOnlyConfig extends Config(
  new WithActiveSPM(Nil) ++
  new WithInclusiveCache(nWays = 8, capacityKB = 2048) ++
  new WithNBanks(1) ++
  new VCU128DualRocketDualGemminiResNet50Config)

// Design: 1 MiB shared L2 plus two independent 1 MiB ActiveSPMs.
class VCU128DualRocketDualGemminiResNet18ActiveSPMConfig extends Config(
  new WithActiveSPM(Seq(
    ActiveSPMParams(
      id = 0,
      controlAddress = AddressSet(0x10050000L, 0xfffL),
      scratchpadAddress = AddressSet(0x70000000L, 0xfffffL),
      spadBeatBytes = 8,
      nBanks = 4,
      externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
      controlXType = NoCrossing),
    ActiveSPMParams(
      id = 1,
      controlAddress = AddressSet(0x10051000L, 0xfffL),
      scratchpadAddress = AddressSet(0x70100000L, 0xfffffL),
      spadBeatBytes = 8,
      nBanks = 4,
      externalMemoryRanges = Seq(AddressSet(0x80000000L, 0x0fffffffL)),
      controlXType = NoCrossing))) ++
  new WithInclusiveCache(nWays = 8, capacityKB = 1024) ++
  new WithNBanks(1) ++
  new VCU128DualRocketDualGemminiResNet50Config)
