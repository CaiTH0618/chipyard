package chipyard

import org.chipsalliance.cde.config.Config
import freechips.rocketchip.subsystem.{MBUS, SBUS}
import freechips.rocketchip.diplomacy.BufferParams

import constellation.channel._
import constellation.routing._
import constellation.router._
import constellation.topology._

import scala.collection.immutable.ListMap


class GemminiLearningConfigBasic extends Config(
  // Improve sim speed by removing TileLink monitors
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++

  new gemmini.DefaultGemminiConfig(
    gemmini.GemminiConfigs.defaultConfig.copy(
      // dma_buswidth = 4 * 8,
      // dma_buswidth = 16 * 8,
      dma_buswidth = 64 * 8,
      shared_scratchpad_config = gemmini.SharedScratchpadConfig(
        // enable = false,
        enable = true,
        global_base_addr = BigInt("F0000000", 16),
        local_size_bytes = 256 * 1024,
        local_banks = 4,
        local_bank_interleaved_bytes = 64,
        // local_bank_interleaved_bytes = 64 * 1024,
        // local_bank_beat_bytes = 8,
        // local_bank_beat_bytes = 16,
        local_bank_beat_bytes = 64,
      )
    )
  ) ++

  new freechips.rocketchip.rocket.WithNBigCores(1) ++
  new chipyard.config.WithSystemBusWidth(16 * 8) ++
  new chipyard.config.AbstractConfig
)


class GemminiLearningConfigSpad extends Config(
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
      // dma_buswidth = 4 * 8,
      // dma_buswidth = 16 * 8,
      dma_buswidth = 64 * 8,
      shared_scratchpad_config = gemmini.SharedScratchpadConfig(
        // enable = false,
        enable = true,
        global_base_addr = BigInt("F0000000", 16),
        local_size_bytes = 256 * 1024,
        local_banks = 4,
        local_bank_interleaved_bytes = 64,
        // local_bank_interleaved_bytes = 64 * 1024,
        // local_bank_beat_bytes = 8,
        // local_bank_beat_bytes = 16,
        local_bank_beat_bytes = 64,
      )
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
  new chipyard.config.WithSystemBusWidth(16 * 8) ++
  // Set number of memory channels.
  new freechips.rocketchip.subsystem.WithNMemoryChannels(4) ++
  new chipyard.config.AbstractConfig
)


class GemminiLearningConfigSpadNoC extends Config (
  // Improve sim speed by removing TileLink monitors
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++

  // System Bus NoC
  /* 0  - 1  - 2  - 3
   * 4  - 5  - 6  - 7
   * 8  - 9  - 10 - 11
   * 12 - 13 - 14 - 15
   */
  new constellation.soc.WithSbusNoC(
    constellation.protocol.SimpleTLNoCParams(
      constellation.protocol.DiplomaticNetworkNodeMapping(
        inNodeMapping = ListMap(
          "Core 0" -> 8, 
          "Core 1" -> 9,  
          "Core 2" -> 10, 
          "Core 3" -> 11,
          "Gemmini0" -> 8,
          "Gemmini1" -> 9,
          "Gemmini2" -> 10,
          "Gemmini3" -> 11,
          "serial_tl" -> 7
        ),
        outNodeMapping = ListMap(
          // Shared scratchpad in Gemmini
          "Gemmini0" -> 8,  
          "Gemmini1" -> 9,
          "Gemmini2" -> 10,
          "Gemmini3" -> 11,
          // Cache banks
          "system[0]" -> 12,  
          "system[1]" -> 13, 
          "system[2]" -> 14, 
          "system[3]" -> 15,
          // Scratchpad banks on SBUS
          "ram[0]" -> 0,  
          "ram[1]" -> 1,
          "ram[2]" -> 2,
          "ram[3]" -> 3,
          "pbus" -> 7
        )
      ),
      constellation.noc.NoCParams(
        topology        = TerminalRouter(Mesh2D(4, 4)),
        channelParamGen = (a, b) => UserChannelParams(Seq.fill(8) { UserVirtualChannelParams(4) }),
        routingRelation = BlockingVirtualSubnetworksRouting(TerminalRouterRouting(Mesh2DEscapeRouting()), 5, 1)
      )
    )
  ) ++

  // Add a Scratchpad to system bus
  new testchipip.soc.WithScratchpad(
    busWhere = SBUS,
    base = 0xC0000000L, 
    size = 1 << 20,  // 1MB
    banks = 4,
  ) ++
  // Remove the default Scratchpad in `AbstractConfig`
  new testchipip.soc.WithNoScratchpads() ++

  // Select a set of tileId/hardId and instantiate one gemmini to each of them.
  // `gemmini_id`` is set inside `WithMultiRoCCGemmini`.
  new chipyard.config.WithMultiRoCCGemmini(
    // Select a set of tileId.
    0, 1, 2, 3
  )(
    gemmini.GemminiConfigs.defaultConfig
  ) ++

  // Enable different RoCCs based on the tileId
  new chipyard.config.WithMultiRoCC ++
  // Set CPU cores
  new freechips.rocketchip.rocket.WithNBigCores(4) ++
  // Set L2 cache.
  new freechips.rocketchip.subsystem.WithInclusiveCache() ++
  // This will set the banking factor of L2 cache.
  new freechips.rocketchip.subsystem.WithNBanks(4) ++
  // Set the width of system bus
  new chipyard.config.WithSystemBusWidth(16 * 8) ++
  // Set number of memory channels.
  new freechips.rocketchip.subsystem.WithNMemoryChannels(4) ++
  new chipyard.config.AbstractConfig
)
