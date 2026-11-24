package chipyard.xdna2

import chisel3._
import chisel3.util._
import freechips.rocketchip.tile.MulAddRecFNPipe

/** One shared opcode is issued to all 32 lanes. Each lane reuses its FMA for
  * multiply/add/subtract; these operations do not instantiate separate FPUs.
  */
object FP32SimdOp {
  val fma = 0.U(2.W)
  val multiply = 1.U(2.W)
  val add = 2.U(2.W)
  val subtract = 3.U(2.W)
}

class FP32SimdLaneResult extends Bundle {
  val data = UInt(32.W)
  // HardFloat order: invalid, divide-by-zero, overflow, underflow, inexact.
  val exceptionFlags = UInt(5.W)
}

class FP32SimdRequest(lanes: Int) extends Bundle {
  val op = UInt(2.W)
  // IEEE/RISC-V rounding modes 0 through 4; no dynamic-CSR rounding mode.
  val roundingMode = UInt(3.W)
  val a = Vec(lanes, UInt(32.W))
  val b = Vec(lanes, UInt(32.W))
  val c = Vec(lanes, UInt(32.W))
}

class FP32SimdResponse(laneCount: Int) extends Bundle {
  val lanes = Vec(laneCount, new FP32SimdLaneResult)
}

class FP32SimdIO(lanes: Int) extends Bundle {
  val in = Flipped(Valid(new FP32SimdRequest(lanes)))
  val out = Valid(new FP32SimdResponse(lanes))
}

/** A three-cycle, fully pipelined IEEE binary32 arithmetic lane.
  * HardFloat supplies recoding, fused arithmetic, rounding and exception flags.
  * There is no flush-to-zero mode. Output valid is input valid delayed by three
  * cycles, and the consumer must accept every valid result (no backpressure).
  */
class FP32SimdLane extends Module {
  val io = IO(new Bundle {
    val valid = Input(Bool())
    val op = Input(UInt(2.W))
    val roundingMode = Input(UInt(3.W))
    val a = Input(UInt(32.W))
    val b = Input(UInt(32.W))
    val c = Input(UInt(32.W))
    val out = Valid(new FP32SimdLaneResult)
  })

  val addSubtract = io.op === FP32SimdOp.add || io.op === FP32SimdOp.subtract
  val multiply = io.op === FP32SimdOp.multiply
  val b = Mux(addSubtract, "h3f800000".U(32.W), io.b)
  // Matching the product sign preserves IEEE signed zero for standalone MUL.
  val productZero = Cat(io.a(31) ^ io.b(31), 0.U(31.W))
  val c = Mux(addSubtract, io.b, Mux(multiply, productZero, io.c))

  val fma = Module(new MulAddRecFNPipe(2, 8, 24))
  fma.io.validin := io.valid
  fma.io.op := Mux(io.op === FP32SimdOp.subtract, 1.U(2.W), 0.U(2.W))
  fma.io.a := hardfloat.recFNFromFN(8, 24, io.a)
  fma.io.b := hardfloat.recFNFromFN(8, 24, b)
  fma.io.c := hardfloat.recFNFromFN(8, 24, c)
  fma.io.roundingMode := io.roundingMode
  fma.io.detectTininess := hardfloat.consts.tininess_afterRounding

  val result = Wire(new FP32SimdLaneResult)
  result.data := hardfloat.fNFromRecFN(8, 24, fma.io.out)
  result.exceptionFlags := fma.io.exceptionFlags
  // Register IEEE conversion/rounding output after the two internal stages.
  io.out := Pipe(fma.io.validout, result, 1)
}

/** Pipelined arithmetic core. FP32SimdEngine supplies register operands,
  * writeback, command scheduling and the connection to the shared scratchpad.
  */
class FP32Simd(lanes: Int) extends Module {
  require(lanes > 0)
  val io = IO(new FP32SimdIO(lanes))
  val arithmetic = Seq.fill(lanes)(Module(new FP32SimdLane))
  for ((lane, index) <- arithmetic.zipWithIndex) {
    lane.io.valid := io.in.valid
    lane.io.op := io.in.bits.op
    lane.io.roundingMode := io.in.bits.roundingMode
    lane.io.a := io.in.bits.a(index)
    lane.io.b := io.in.bits.b(index)
    lane.io.c := io.in.bits.c(index)
    io.out.bits.lanes(index) := lane.io.out.bits
  }
  io.out.valid := arithmetic.head.io.out.valid
}
