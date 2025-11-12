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
  ) ++
  
  // Add a Scratchpad to memory bus
  new testchipip.soc.WithScratchpad(
    busWhere = MBUS,
    base = 0x08000000L, 
    size = 1 << 20,  // 1MB
    banks = 4,
  ) ++

  // Remove the default Scratchpad in `AbstractConfig`
  new testchipip.soc.WithNoScratchpads() ++

  // Select a set of tileId/hardId and instantiate one gemmini to each of them.
  new chipyard.config.WithMultiRoCCGemmini(
    // Select a set of tileId.
    0, 1, 2, 3
  )(
   gemmini.GemminiConfigs.defaultConfig.copy(
      // The `dma_buswidth` should be the same as system bus width.
      dma_buswidth = 128,
    )
  ) ++

  // Enable different RoCCs based on the tileId
  new chipyard.config.WithMultiRoCC ++

  // Set CPU cores
  new freechips.rocketchip.rocket.WithNBigCores(4) ++

  // Set L2 cache.
  new freechips.rocketchip.subsystem.WithInclusiveCache(
    nWays = 16,
    capacityKB = 512,
  ) ++

  // This will set the banking factor of L2 cache.
  new freechips.rocketchip.subsystem.WithNBanks(4) ++

  // Set the width of system bus
  new chipyard.config.WithSystemBusWidth(128) ++

  // Set number of memory channels.
  new freechips.rocketchip.subsystem.WithNMemoryChannels(4) ++
  
  new chipyard.config.AbstractConfig
)
