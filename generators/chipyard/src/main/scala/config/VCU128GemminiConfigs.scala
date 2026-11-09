package chipyard

import org.chipsalliance.cde.config.Config
import gemmini._
import gemmini.Arithmetic.SIntArithmetic

// Intended for the user's supplied GemminiArrayConfig and RoCCFragments version.
// Parameter checks were evaluated offline; run Chipyard elaboration before use.
object VCU128GemminiParameters {
  val bringup8x8 = GemminiConfigs.defaultConfig.copy(
    tileRows = 1,
    tileColumns = 1,
    meshRows = 8,
    meshColumns = 8,
    dataflow = Dataflow.WS,

    sp_capacity = CapacityInKilobytes(64),
    acc_capacity = CapacityInKilobytes(32),
    sp_banks = 4,
    acc_banks = 2,
    sp_singleported = true,
    acc_singleported = false,

    // Match the baseline 64-bit system bus; confirm all downstream modules
    // accept this setting during elaboration and compare ChipTop ports.
    dma_buswidth = 64,
    dma_maxbytes = 64,

    // Preserve real SInt arithmetic from defaultConfig, never dummyConfig.
   // mvin_scale_args = None,
   // mvin_scale_acc_args = None,
    mvin_scale_shared = false,
   // acc_scale_args = None,
    acc_read_full_width = true,

    has_training_convs = false,
    has_max_pool = false,
    has_nonlinear_activations = false,
    has_dw_convs = false,
    has_normalizations = false,
    has_first_layer_optimizations = false,

    shared_scratchpad_config = SharedScratchpadConfig(enable = false),
    use_shared_ext_mem = false,
    use_dedicated_tl_port = true,
    clock_gate = false,

    // Keep the original pipeline settings until their implementation is checked.
    tile_latency = 0,
    mesh_output_delay = 1,
    headerFileName = "gemmini_params.h"
  )

    // Model inference configuration derived from the validated 8x8 baseline.
  val resnet50_8x8 = bringup8x8.copy(
    has_max_pool = true,
    has_nonlinear_activations = true
  )

}

class VCU128DualRocketDualGemminiConfig extends Config(
  new chipyard.iobinders.WithL2FBusAXI4Punchthrough ++
  new freechips.rocketchip.subsystem.WithDefaultSlavePort ++
  new chipyard.config.WithMultiRoCCGemmini(0, 1)(
    VCU128GemminiParameters.bringup8x8
  ) ++
  new chipyard.config.WithMultiRoCC ++
  new chipyard.config.WithSystemBusWidth(64) ++
  new DualRocketConfig
)



class VCU128DualRocketDualGemminiResNet50Config extends Config(
  new chipyard.iobinders.WithL2FBusAXI4Punchthrough ++
  new freechips.rocketchip.subsystem.WithDefaultSlavePort ++
  new chipyard.config.WithMultiRoCCGemmini(0, 1)(
    VCU128GemminiParameters.resnet50_8x8
  ) ++
  new chipyard.config.WithMultiRoCC ++
  new chipyard.config.WithSystemBusWidth(64) ++
  new DualRocketConfig
)
