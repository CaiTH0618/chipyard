package chipyard.xdna2

import chisel3._
import chisel3.util._
import gemmini.{ExtMemIO, ExtSpadMemIO}

class SimdScratchpadRequest extends Bundle {
  val byteAddress = UInt(16.W)
  val write = Bool()
  val data = UInt(256.W)
}

/** One 32-byte access; every accepted read returns exactly one cycle later. */
class SimdScratchpadIO extends Bundle {
  val request = Decoupled(new SimdScratchpadRequest)
  val response = Flipped(Valid(UInt(256.W)))
}

class SimdBankRequest(depth: Int, width: Int) extends Bundle {
  val row = UInt(log2Ceil(depth).W)
  val write = Bool()
  val data = UInt(width.W)
}

/** A physical single-read/write-port SRAM. Gemmini has no request-ready signal
  * at this boundary, so its requests always win and only SIMD can be stalled.
  */
class AIETileSramBank(depth: Int, width: Int, shareSimd: Boolean) extends Module {
  require(width % 8 == 0)
  val io = IO(new Bundle {
    val gemmini = Flipped(new ExtMemIO)
    val simdRequest = if (shareSimd) Some(Flipped(Decoupled(new SimdBankRequest(depth, width)))) else None
    val simdResponse = if (shareSimd) Some(Valid(UInt(width.W))) else None
  })
  val native = io.gemmini
  val nativeBusy = native.read_en || native.write_en
  val simdFire = io.simdRequest.map(_.fire).getOrElse(false.B)
  val simdWrite = io.simdRequest.map(_.bits.write).getOrElse(false.B)
  val simdRow = io.simdRequest.map(_.bits.row).getOrElse(0.U)
  val simdData = io.simdRequest.map(_.bits.data).getOrElse(0.U(width.W))
  io.simdRequest.foreach(_.ready := !nativeBusy)

  val nativeRow = Mux(native.write_en, native.write_addr, native.read_addr)
  val address = Wire(UInt(log2Ceil(depth).W))
  address := Mux(nativeBusy, nativeRow, simdRow)
  val write = native.write_en || (simdFire && simdWrite)
  val data = Wire(UInt(width.W))
  data := Mux(nativeBusy, native.write_data, simdData)
  val mask = Wire(UInt((width / 8).W))
  mask := Mux(nativeBusy, native.write_mask, Fill(width / 8, 1.U(1.W)))
  val memory = SyncReadMem(depth, Vec(width / 8, UInt(8.W)))
  val readData = memory.readWrite(address, data.asTypeOf(Vec(width / 8, UInt(8.W))),
    mask.asBools, nativeBusy || simdFire, write).asUInt
  native.read_data := readData
  io.simdResponse.foreach { response =>
    response.valid := RegNext(simdFire && !simdWrite, false.B)
    response.bits := readData
  }
  assert(!(native.read_en && native.write_en), "Gemmini requested two accesses to a single-port SRAM")
}

/** Replaces Gemmini's internal SRAMs through its existing external-memory IO.
  * Scratchpad byte addresses retain Gemmini's bank-contiguous layout:
  * bits [15:14] select one 16-KiB bank, [13:5] select a 32-byte row.
  * Accumulator sub-banks remain private to Gemmini.
  */
class AIETileSharedMemory(spBanks: Int, spRows: Int, spBits: Int,
    accBanks: Int, accSubBanks: Int, accRows: Int, accBits: Int) extends Module {
  require(spBanks == 4 && spRows == 512 && spBits == 256)
  val io = IO(new Bundle {
    val gemmini = Flipped(new ExtSpadMemIO(spBanks, accBanks, accSubBanks))
    val simd = Flipped(new SimdScratchpadIO)
  })
  val banks = Seq.fill(spBanks)(Module(new AIETileSramBank(spRows, spBits, true)))
  val selected = io.simd.request.bits.byteAddress(15, 14)
  for ((bank, index) <- banks.zipWithIndex) {
    bank.io.gemmini <> io.gemmini.spad(index)
    bank.io.simdRequest.get.valid := io.simd.request.valid && selected === index.U
    bank.io.simdRequest.get.bits.row := io.simd.request.bits.byteAddress(13, 5)
    bank.io.simdRequest.get.bits.write := io.simd.request.bits.write
    bank.io.simdRequest.get.bits.data := io.simd.request.bits.data
  }
  io.simd.request.ready := VecInit(banks.map(_.io.simdRequest.get.ready))(selected)
  val responses = banks.map(_.io.simdResponse.get)
  io.simd.response.valid := responses.map(_.valid).reduce(_ || _)
  io.simd.response.bits := Mux1H(responses.map(_.valid), responses.map(_.bits))
  for (bank <- 0 until accBanks; subBank <- 0 until accSubBanks) {
    val sram = Module(new AIETileSramBank(accRows, accBits, false))
    sram.io.gemmini <> io.gemmini.acc(bank)(subBank)
  }
  when (io.simd.request.valid) {
    assert(io.simd.request.bits.byteAddress(4, 0) === 0.U)
  }
  assert(PopCount(VecInit(responses.map(_.valid))) <= 1.U)
}
