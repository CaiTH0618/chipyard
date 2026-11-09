package chipyard

import org.chipsalliance.cde.config.Config

class VCU128RocketConfig extends Config(
  new chipyard.iobinders.WithL2FBusAXI4Punchthrough ++
  new freechips.rocketchip.subsystem.WithDefaultSlavePort ++
  new RocketConfig
)


class VCU128DualRocketConfig extends Config(
  new chipyard.iobinders.WithL2FBusAXI4Punchthrough ++
  new freechips.rocketchip.subsystem.WithDefaultSlavePort ++
  new DualRocketConfig
)

