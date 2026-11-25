package chipyard

import cnpu.{CNPUParams, WithCNPU}
import org.chipsalliance.cde.config.Config

/** One Rocket hart with two CNPU cores for MMIO integration testing. */
class CNPUScaffoldRocketConfig extends Config(
  new WithCNPU(CNPUParams(baseAddress = 0x10060000L, nCores = 2)) ++
  new RocketConfig)
