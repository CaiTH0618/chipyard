package chipyard

import org.chipsalliance.cde.config.Config


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

  // Add a Scratchpad to memory bus
  new testchipip.soc.WithSbusScratchpad(base = 0xC0000000L, size = 1 << 20) ++
  new testchipip.soc.WithMbusScratchpad(base = 0x08000000L, size = 1 << 20) ++
  // Remove the default Scratchpad in `AbstractConfig`
  new testchipip.soc.WithNoScratchpads() ++

  new gemmini.DefaultGemminiConfig ++

  new freechips.rocketchip.rocket.WithNBigCores(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig
)
