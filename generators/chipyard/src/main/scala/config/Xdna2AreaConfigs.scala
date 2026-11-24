package chipyard

import chipyard.xdna2.{Xdna2AreaModuleKey, Xdna2DeSCQueueDepth, Xdna2SwitchKey, Xdna2SwitchParams}
import freechips.rocketchip.rocket.{DCacheParams, RocketCoreParams}
import freechips.rocketchip.subsystem.{CacheBlockBytes, InSubsystem, SystemBusKey, SystemBusParams, TilesLocated}
import freechips.rocketchip.tile.{MaxHartIdBits, RocketTileParams, TileKey}
import org.chipsalliance.cde.config.Config
import org.chipsalliance.diplomacy.nodes.MonitorsEnabled

/** Parameter context only: none of these configs instantiates a CPU or a SoC. */
class Xdna2AreaBaseConfig extends Config((site, here, up) => {
  case CacheBlockBytes => 64
  case SystemBusKey => SystemBusParams(beatBytes = Xdna2ArrayParams().networkBeatBytes, blockBytes = 64)
  case TileKey => RocketTileParams(
    core = RocketCoreParams(useHypervisor = false, useDebug = false),
    dcache = Some(DCacheParams(rowBits = Xdna2ArrayParams().networkDataBits)))
  case MaxHartIdBits => 1
  case TilesLocated(InSubsystem) => Nil
  case MonitorsEnabled => false
})

/** INT8 Gemmini plus a 32-lane FP32 SIMD; total tile SRAM remains 128 KiB. */
class Xdna2AIETileConfig extends Config(
  new Config((site, here, up) => { case Xdna2AreaModuleKey => "aie" }) ++
  new Xdna2AreaBaseConfig)

class Xdna2MemTileConfig extends Config(
  new Config((site, here, up) => { case Xdna2AreaModuleKey => "mem" }) ++
  new Xdna2AreaBaseConfig)

class Xdna2DeSCMemTileConfig extends Config(
  new Config((site, here, up) => { case Xdna2DeSCQueueDepth => 8 }) ++
  new Xdna2MemTileConfig)

/** Interior compute location (x=3, y=2) in the full 8x5 mesh. */
class Xdna2InterconnectSwitchConfig extends Config(
  new Config((site, here, up) => { case Xdna2AreaModuleKey => "switch" }) ++
  new Xdna2AreaBaseConfig)

class WithXdna2SwitchLocation(x: Int, y: Int) extends Config((site, here, up) => {
  case Xdna2SwitchKey => Xdna2SwitchParams(x, y)
})

class Xdna2EdgeSwitchConfig extends Config(
  new WithXdna2SwitchLocation(0, 2) ++ new Xdna2InterconnectSwitchConfig)

class Xdna2CornerSwitchConfig extends Config(
  new WithXdna2SwitchLocation(0, 4) ++ new Xdna2InterconnectSwitchConfig)

class Xdna2MemSwitchConfig extends Config(
  new WithXdna2SwitchLocation(3, 0) ++ new Xdna2InterconnectSwitchConfig)
