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
    // banks = 1,
    // banks = 2,
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
    // banks = 1,
    // banks = 2,
    banks = 4,
    subBanks = 1,
    buffer = BufferParams(8),
    outerBuffer = BufferParams(8),
  ) ++
  // Remove the default Scratchpad in `AbstractConfig`
  new testchipip.soc.WithNoScratchpads() ++

  // Select a set of tileId/hardId and instantiate one gemmini to each of them.
  new chipyard.config.WithMultiRoCCGemmini(
    // Select a set of tileId.
    // 0
    0, 1, 2, 3
  )(
   gemmini.GemminiConfigs.defaultConfig.copy(
      // The `dma_buswidth` should be at least the same as system bus width.
      // dma_buswidth = 64,
      dma_buswidth = 128,
      // dma_buswidth = 256,
      // dma_buswidth = 512,
    )
  ) ++
  // Enable different RoCCs based on the tileId
  new chipyard.config.WithMultiRoCC ++

  // new freechips.rocketchip.rocket.WithNHugeCores(1) ++
  new freechips.rocketchip.rocket.WithNHugeCores(4) ++

  // new chipyard.config.WithSystemBusWidth(64) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  // new chipyard.config.WithSystemBusWidth(256) ++
  // new chipyard.config.WithSystemBusWidth(512) ++

  new freechips.rocketchip.subsystem.WithInclusiveCache(
    capacityKB = 64,
    // capacityKB = 512,
  ) ++
  new chipyard.config.AbstractConfig
)
