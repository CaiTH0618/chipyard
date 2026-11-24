package chipyard.xdna2

import chisel3._
import chisel3.util._

object FP32SimdCommandKind {
  val load = 0.U(2.W)
  val store = 1.U(2.W)
  val compute = 2.U(2.W)
}

class FP32SimdCommand(registers: Int) extends Bundle {
  val kind = UInt(2.W)
  val tag = UInt(8.W)
  val destination = UInt(log2Ceil(registers).W)
  val sourceA = UInt(log2Ceil(registers).W)
  val sourceB = UInt(log2Ceil(registers).W)
  val sourceC = UInt(log2Ceil(registers).W)
  val op = UInt(2.W)
  val roundingMode = UInt(3.W)
  // LOAD/STORE transfer 128 bytes at a 128-byte-aligned scratchpad address.
  val byteAddress = UInt(16.W)
}

class FP32SimdCompletion(registers: Int) extends Bundle {
  val kind = UInt(2.W)
  val tag = UInt(8.W)
  val register = UInt(log2Ceil(registers).W)
  val exceptionFlags = UInt(5.W)
  val error = Bool()
}

class FP32SimdControlIO(registers: Int) extends Bundle {
  val command = Flipped(Decoupled(new FP32SimdCommand(registers)))
  val completion = Decoupled(new FP32SimdCompletion(registers))
  val busy = Output(Bool())
}

/** Eight 1024-bit registers, with three asynchronous vector reads, one vector
  * writeback port and a 256-bit LOAD port. Storage is flops, counted as logic.
  * LOAD and compute writeback are mutually exclusive in the engine.
  */
class FP32VectorRegisterFile(registerCount: Int, lanes: Int) extends Module {
  require(isPow2(registerCount) && registerCount >= 4 && lanes == 32)
  val words = lanes * 32 / 256
  val indexBits = log2Ceil(registerCount)
  val io = IO(new Bundle {
    val readIndices = Input(Vec(3, UInt(indexBits.W)))
    val readData = Output(Vec(3, Vec(lanes, UInt(32.W))))
    val writeVector = Flipped(Valid(new Bundle {
      val index = UInt(indexBits.W)
      val data = Vec(lanes, UInt(32.W))
    }))
    val writeWord = Flipped(Valid(new Bundle {
      val index = UInt(indexBits.W)
      val word = UInt(log2Ceil(words).W)
      val data = UInt(256.W)
    }))
  })
  val storage = Reg(Vec(registerCount, Vec(words, UInt(256.W))))
  for (port <- 0 until 3) {
    io.readData(port) := storage(io.readIndices(port)).asUInt.asTypeOf(Vec(lanes, UInt(32.W)))
  }
  when (io.writeVector.valid) {
    storage(io.writeVector.bits.index) := io.writeVector.bits.data.asUInt.asTypeOf(Vec(words, UInt(256.W)))
  }
  when (io.writeWord.valid) {
    storage(io.writeWord.bits.index)(io.writeWord.bits.word) := io.writeWord.bits.data
  }
  assert(!(io.writeVector.valid && io.writeWord.valid))
}

/** Register-based SIMD command engine. Independent computations issue each
  * cycle; a scoreboard stalls RAW/WAW hazards. Vector loads/stores drain the
  * arithmetic pipeline and serialize against further commands. Completion
  * credits reserve space before issue, so a blocked consumer cannot drop FMA
  * results or memory-transfer completions.
  */
class FP32SimdEngine(lanes: Int, registers: Int) extends Module {
  require(lanes == 32 && isPow2(registers) && registers >= 4)
  private val completionDepth = 8
  val io = IO(new Bundle {
    val control = new FP32SimdControlIO(registers)
    val scratchpad = new SimdScratchpadIO
  })
  val arithmetic = Module(new FP32Simd(lanes))
  val registerFile = Module(new FP32VectorRegisterFile(registers, lanes))
  val completions = Module(new Queue(new FP32SimdCompletion(registers), completionDepth))
  io.control.completion <> completions.io.deq

  val idle :: load :: store :: Nil = Enum(3)
  val state = RegInit(idle)
  val pending = Reg(new FP32SimdCommand(registers))
  val issuedWords = RegInit(0.U(3.W))
  val returnedWords = RegInit(0.U(3.W))
  val registerBusy = RegInit(0.U(registers.W))
  val outstanding = RegInit(0.U(log2Ceil(completionDepth + 1).W))
  val command = io.control.command.bits
  val isCompute = command.kind === FP32SimdCommandKind.compute
  val isLoad = command.kind === FP32SimdCommandKind.load
  val isStore = command.kind === FP32SimdCommandKind.store
  val legalCompute = isCompute && command.roundingMode <= 4.U
  val legalTransfer = (isLoad || isStore) && command.byteAddress(6, 0) === 0.U
  val legal = legalCompute || legalTransfer
  val hazards = registerBusy(command.sourceA) || registerBusy(command.sourceB) ||
    (command.op === FP32SimdOp.fma && registerBusy(command.sourceC)) || registerBusy(command.destination)
  io.control.command.ready := state === idle && outstanding < completionDepth.U &&
    Mux(legalCompute, !hazards, !registerBusy.orR)
  val issueCompute = io.control.command.fire && legalCompute
  val issueTransfer = io.control.command.fire && legalTransfer
  val issueError = io.control.command.fire && !legal
  io.control.busy := outstanding =/= 0.U

  registerFile.io.readIndices(0) := Mux(state === store, pending.sourceA, command.sourceA)
  registerFile.io.readIndices(1) := command.sourceB
  registerFile.io.readIndices(2) := command.sourceC
  arithmetic.io.in.valid := issueCompute
  arithmetic.io.in.bits.op := command.op
  arithmetic.io.in.bits.roundingMode := command.roundingMode
  arithmetic.io.in.bits.a := registerFile.io.readData(0)
  arithmetic.io.in.bits.b := registerFile.io.readData(1)
  arithmetic.io.in.bits.c := registerFile.io.readData(2)

  val responseTag = Wire(new FP32SimdCompletion(registers))
  responseTag.kind := command.kind
  responseTag.tag := command.tag
  responseTag.register := Mux(isStore, command.sourceA, command.destination)
  responseTag.exceptionFlags := 0.U
  responseTag.error := false.B
  val writebackTag = Pipe(issueCompute, responseTag, 3)
  registerFile.io.writeVector.valid := arithmetic.io.out.valid
  registerFile.io.writeVector.bits.index := writebackTag.bits.register
  registerFile.io.writeVector.bits.data := VecInit(arithmetic.io.out.bits.lanes.map(_.data))
  assert(writebackTag.valid === arithmetic.io.out.valid)

  when (issueTransfer) {
    pending := command
    issuedWords := 0.U
    returnedWords := 0.U
    state := Mux(isLoad, load, store)
  }
  io.scratchpad.request.valid := (state === load || state === store) && issuedWords < 4.U
  io.scratchpad.request.bits.byteAddress := pending.byteAddress + (issuedWords(1, 0) << 5)
  io.scratchpad.request.bits.write := state === store
  io.scratchpad.request.bits.data := registerFile.io.readData(0).asUInt.asTypeOf(Vec(4, UInt(256.W)))(issuedWords(1, 0))
  when (io.scratchpad.request.fire) { issuedWords := issuedWords + 1.U }
  registerFile.io.writeWord.valid := io.scratchpad.response.valid
  registerFile.io.writeWord.bits.index := pending.destination
  registerFile.io.writeWord.bits.word := returnedWords(1, 0)
  registerFile.io.writeWord.bits.data := io.scratchpad.response.bits
  when (io.scratchpad.response.valid) {
    assert(state === load && returnedWords < issuedWords)
    returnedWords := returnedWords + 1.U
  }
  val loadDone = state === load && io.scratchpad.response.valid && returnedWords === 3.U
  val storeDone = state === store && io.scratchpad.request.fire && issuedWords === 3.U
  when (loadDone || storeDone) { state := idle }

  val setBusy = Mux(issueCompute || (issueTransfer && isLoad),
    UIntToOH(command.destination, registers), 0.U(registers.W))
  val clearBusy = Mux(arithmetic.io.out.valid, UIntToOH(writebackTag.bits.register, registers),
    Mux(loadDone, UIntToOH(pending.destination, registers), 0.U(registers.W)))
  registerBusy := (registerBusy & ~clearBusy) | setBusy

  completions.io.enq.valid := arithmetic.io.out.valid || loadDone || storeDone || issueError
  completions.io.enq.bits := responseTag
  when (arithmetic.io.out.valid) {
    completions.io.enq.bits := writebackTag.bits
    completions.io.enq.bits.exceptionFlags := arithmetic.io.out.bits.lanes.map(_.exceptionFlags).reduce(_ | _)
  }.elsewhen (loadDone || storeDone) {
    completions.io.enq.bits.kind := pending.kind
    completions.io.enq.bits.tag := pending.tag
    completions.io.enq.bits.register := Mux(state === store, pending.sourceA, pending.destination)
  }.elsewhen (issueError) {
    completions.io.enq.bits.error := true.B
  }
  when (io.control.command.fire =/= io.control.completion.fire) {
    outstanding := Mux(io.control.command.fire, outstanding + 1.U, outstanding - 1.U)
  }
  assert(outstanding <= completionDepth.U)
  assert(PopCount(VecInit(Seq(arithmetic.io.out.valid, loadDone, storeDone, issueError))) <= 1.U)
  when (completions.io.enq.valid) { assert(completions.io.enq.ready) }
}
