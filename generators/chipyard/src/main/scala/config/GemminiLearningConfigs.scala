package chipyard

import org.chipsalliance.cde.config.Config
import freechips.rocketchip.subsystem.{MBUS, SBUS}
import freechips.rocketchip.diplomacy.BufferParams


class GemminiLearningConfigBasic extends Config(
  // Improve sim speed by removing TileLink monitors
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++

  new gemmini.DefaultGemminiConfig ++

  new freechips.rocketchip.rocket.WithNBigCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig
)


class GemminiLearningConfigWithScratchpad extends Config(
  // Improve sim speed by removing TileLink monitors
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++

  // Add a Scratchpad to system bus
  new testchipip.soc.WithScratchpad(
    busWhere = SBUS,
    base = 0xC0000000L, 
    size = 1 << 20,  // 1MB
    banks = 4,
    subBanks = 1,
    buffer = BufferParams(8),
    outerBuffer = BufferParams(8),
  ) ++
  // Add a Scratchpad to memory bus
  new testchipip.soc.WithScratchpad(
    busWhere = MBUS,
    base = 0x08000000L, 
    size = 1 << 20,  // 1MB
    banks = 4,
    subBanks = 1,
    buffer = BufferParams(8),
    outerBuffer = BufferParams(8),
  ) ++
  // Remove the default Scratchpad in `AbstractConfig`
  new testchipip.soc.WithNoScratchpads() ++

  new gemmini.DefaultGemminiConfig ++

  new freechips.rocketchip.rocket.WithNBigCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig
)
