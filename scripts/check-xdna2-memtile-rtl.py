#!/usr/bin/env python3
"""Exercise generated MemTile RTL directly; no CPU, firmware or software tests."""

import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RTL = ROOT / "sims/verilator/generated-src/chipyard.xdna2.Xdna2AreaHarness.Xdna2MemTileConfig"
DESC_RTL = DEFAULT_RTL.with_name("chipyard.xdna2.Xdna2AreaHarness.Xdna2DeSCMemTileConfig")


DESC_TASKS = r"""
integer decoder_issued=0;
integer decoder_before, infer_before, weighted_decoder, before_release, paused_decoder, paused_inference;
integer pause_external=0, max_decoder_credit=0;

always @(posedge clock) if (!reset) begin
  if (io_dma_a_valid && io_dma_a_ready && io_dma_a_bits_address >= 32'h80008000)
    decoder_issued=decoder_issued+1;
  if (io_desc_inFlightBursts_1>max_decoder_credit)
    max_decoder_credit=io_desc_inFlightBursts_1;
end

task configure(input [3:0] weight);
  begin
    @(negedge clock);
    io_desc_configure_bits_decoderWeight=weight;
    io_desc_configure_bits_burstLimit_0=64; io_desc_configure_bits_byteLimit_0=4096;
    // Byte credit limits decoding to 16 despite the larger burst limit.
    io_desc_configure_bits_burstLimit_1=24; io_desc_configure_bits_byteLimit_1=1024;
    io_desc_configure_valid=1;
    @(negedge clock); io_desc_configure_valid=0;
  end
endtask

task decode_burst(input bit store, input integer ext_offset, input integer local_offset);
  reg accepted;
  begin
    @(negedge clock);
    io_desc_decode_bits_externalBlockAddress=(32'h80000000+ext_offset)>>6;
    io_desc_decode_bits_scratchpadBlockOffset=local_offset>>6;
    io_desc_decode_bits_store=store;
    io_desc_decode_valid=1; accepted=0;
    while (!accepted) begin @(posedge clock); accepted=io_desc_decode_ready; #1; end
    @(negedge clock); io_desc_decode_valid=0;
  end
endtask
"""

DESC_SCENARIO = r"""
  requests_enabled=0; responses_enabled=0;
  configure(4);
  launch(0,0,0,8192);
  fork
    begin
      for (integer b=0;b<64;b=b+1) decode_burst(0,32'h8000+b*64,32'h2000+b*64);
    end
    begin
      // Build both backlogs, then ignore the few requests already staged
      // before both workloads were ready. Count only real external A fires.
      repeat (90) begin @(posedge clock); #1; end
      requests_enabled=1;
      while (dma_count<4) begin @(posedge clock); #1; end
      decoder_before=decoder_issued;
      while (dma_count<36) begin @(posedge clock); #1; end
      weighted_decoder=decoder_issued-decoder_before;
      if (weighted_decoder!=8)
        $fatal(1,"w=4 expected 8 decode bursts out of 32, observed %0d",weighted_decoder);

      // Change the weight under A-channel backpressure with D still stalled.
      // Allow only preexisting TLBuffer entries and one held selection.
      requests_enabled=0;
      repeat (12) begin @(posedge clock); #1; end
      before_release=io_desc_inFlightBursts_1;
      decoder_before=decoder_issued;
      configure(0);
      requests_enabled=1;
      repeat (16) begin @(posedge clock); #1; end
      if (decoder_issued<before_release || decoder_issued>before_release+1)
        $fatal(1,"w=0 let a new decode request through");
      pause_external=decoder_issued-decoder_before;
      paused_decoder=decoder_issued;
      infer_before=dma_count-decoder_issued;
      responses_enabled=1;
      finish_dma(0,8192);
      while (io_desc_completedBytes_1 < paused_decoder*64) begin @(posedge clock); #1; end
      paused_inference=dma_count-decoder_issued-infer_before;
      if (decoder_issued!=paused_decoder || paused_inference<32 ||
          io_desc_inFlightBursts_1!=0 || io_desc_completedBytes_0!=8192 ||
          io_desc_completedBytes_1!=paused_decoder*64)
        $fatal(1,"pause did not drain decoding and let inference continue");
      check_sram_copy(0,0,8192,8192);

      // A waiting decoder must not prevent the existing byte DMA from running.
      launch(0,32'h6803,32'h6005,17);
      finish_dma(0,17);
      if (decoder_issued!=paused_decoder) $fatal(1,"decoder issued during byte DMA");
      configure(4);
    end
  join
  while (io_desc_completedBytes_1<4096) begin @(posedge clock); #1; end
  if (decoder_issued!=64 || io_desc_decodeError) $fatal(1,"decode load resume failed");
  check_sram_copy(32'h2000,32'h8000,4096,4096);

  // Exercise the shared STORE path and verify the independently programmed
  // byte limit with responses held and more stores queued.
  responses_enabled=0;
  for (j=0;j<24;j=j+1) decode_burst(1,32'hc000+j*64,32'h2000+j*64);
  while (io_desc_inFlightBursts_1<16) begin @(posedge clock); #1; end
  repeat (32) begin @(posedge clock); #1; end
  if (decoder_issued!=80 || io_desc_inFlightBursts_1!=16)
    $fatal(1,"decode byte credit limit failed");
  responses_enabled=1;
  while (io_desc_completedBytes_1<5632) begin @(posedge clock); #1; end
  for (j=0;j<1536;j=j+1)
    if (memory[32'hc000+j]!==memory[32'h8000+j]) $fatal(1,"decode STORE data mismatch %0d",j);
  if (max_decoder_credit!=16 || io_desc_inFlightBursts_1!=0 || io_desc_decodeError)
    $fatal(1,"decode credit return failed");
  $display("PASS DeSC MemTile RTL cycles=%0d weighted_bursts=32 weighted_decoder=%0d previously_committed_decoder=%0d inference_after_pause=%0d peak_decoder_in_flight=%0d inference_bytes=%0d decoder_bytes=%0d",
    cycle,weighted_decoder,pause_external,paused_inference,max_decoder_credit,
    io_desc_completedBytes_0,io_desc_completedBytes_1);
  $finish;
end
endmodule
"""


def bench(rtl):
    text = (rtl / "gen-collateral/MemTile.sv").read_text()
    header = re.search(r"\bmodule MemTile\s*\((.*?)\);", text, re.S)[1]
    ports = {}
    for line in header.splitlines():
        m = re.match(r"\s*(input|output)\s+(?:\[(\d+):0\]\s+)?(\w+)", line)
        if m:
            direction, high, name = m.groups()
            ports[name] = (direction, int(high) + 1 if high else 1)
    desc = "io_desc_configure_valid" in ports
    lines = ["`timescale 1ns/1ps", "module tb;", "reg clock=0, reset=1;", "always #5 clock=~clock;"]
    initial = []
    for name, (direction, width) in ports.items():
        if name in ("clock", "reset"):
            continue
        lines.append(f"{'reg' if direction == 'input' else 'wire'} [{width-1}:0] {name};")
        if direction == "input":
            initial.append(f"{name}=0;")
    lines.append("MemTile dut(" + ",".join(f".{name}({name})" for name in ports) + ");")
    lines.append(r"""
integer cycle=0, ctrl_count=0, sp_count=0, sp_issued=0, max_sp_pending=0;
integer dma_count=0, max_pending=0, j, k, model_index, response_index, pick, pending_now;
integer snapshot, start_count, before_dma, checks=0, dual_local_cycles=0;
reg [63:0] ctrl_result;
reg [511:0] sp_result;
reg [511:0] sp_results[0:2047];
reg [7:0] memory[0:65535];
reg pending[0:63];
reg [31:0] pending_address[0:63];
reg [2:0] pending_opcode[0:63], pending_size[0:63];
reg pending_bad[0:63];
reg requests_enabled=1, responses_enabled=1, response_held=0, inject_error=0;
reg [31:0] fault_address=0;
reg [63:0] value;
reg [511:0] data_value, expected_value;

always @(posedge clock) if (!reset) begin
  cycle=cycle+1;
  if (dut.active.dma.fast.io_local_0_a_valid && dut.active.dma.fast.io_local_0_a_ready &&
      dut.active.dma.fast.io_local_1_a_valid && dut.active.dma.fast.io_local_1_a_ready)
    dual_local_cycles=dual_local_cycles+1;
  if (cycle>100000) $fatal(1,"MemTile RTL timeout");
  if (io_control_d_valid && io_control_d_ready) begin
    if (io_control_d_bits_denied || io_control_d_bits_corrupt) $fatal(1,"MMIO response error");
    ctrl_result=io_control_d_bits_data; ctrl_count=ctrl_count+1;
  end
  if (io_scratchpad_a_valid && io_scratchpad_a_ready) sp_issued=sp_issued+1;
  if (io_scratchpad_d_valid && io_scratchpad_d_ready) begin
    if (io_scratchpad_d_bits_denied || io_scratchpad_d_bits_corrupt) $fatal(1,"global SRAM response error");
    sp_result=io_scratchpad_d_bits_data;
    sp_results[io_scratchpad_d_bits_source]=io_scratchpad_d_bits_data;
    sp_count=sp_count+1;
  end
  if (sp_issued-sp_count>max_sp_pending) max_sp_pending=sp_issued-sp_count;
  if (io_dma_d_valid && io_dma_d_ready) begin
    if (!pending[io_dma_d_bits_source]) $fatal(1,"external response with no request");
    pending[io_dma_d_bits_source]=0; response_held=0;
  end
  if (io_dma_a_valid && io_dma_a_ready) begin
    if (pending[io_dma_a_bits_source]) $fatal(1,"external source reused while pending");
    if (io_dma_a_bits_address < 32'h80000000 || io_dma_a_bits_address >= 32'h80010000)
      $fatal(1,"DMA escaped configured test memory");
    pending[io_dma_a_bits_source]=1;
    pending_address[io_dma_a_bits_source]=io_dma_a_bits_address;
    pending_size[io_dma_a_bits_source]=io_dma_a_bits_size;
    pending_opcode[io_dma_a_bits_source]=io_dma_a_bits_opcode;
    pending_bad[io_dma_a_bits_source]=inject_error && io_dma_a_bits_address==fault_address;
    dma_count=dma_count+1;
    if (io_dma_a_bits_opcode==0 || io_dma_a_bits_opcode==1) begin
      if (!(inject_error && io_dma_a_bits_address==fault_address))
        for (model_index=0;model_index<64;model_index=model_index+1) if (io_dma_a_bits_mask[model_index])
          memory[(io_dma_a_bits_address & 32'hffffffc0)-32'h80000000+model_index]=io_dma_a_bits_data[model_index*8+:8];
    end else if (io_dma_a_bits_opcode!=4) $fatal(1,"unexpected DMA opcode");
  end
  pending_now=0;
  for (model_index=0;model_index<64;model_index=model_index+1) if (pending[model_index]) pending_now=pending_now+1;
  if (pending_now>max_pending) max_pending=pending_now;
end

// Vary request readiness and deliberately return the highest pending source
// first. Holding all responses initially verifies all 64 IDs are usable.
always @(negedge clock) if (!reset) begin
  io_dma_a_ready=requests_enabled && (cycle%7 != 0);
  if (!response_held) begin
    io_dma_d_valid=0; pick=-1;
    if (responses_enabled && cycle%5 != 0)
      for (response_index=0;response_index<64;response_index=response_index+1) if (pending[response_index]) pick=response_index;
    if (pick>=0) begin
      response_held=1; io_dma_d_valid=1;
      io_dma_d_bits_source=pick;
      io_dma_d_bits_size=pending_size[pick];
      io_dma_d_bits_opcode=(pending_opcode[pick]==4) ? 1 : 0;
      io_dma_d_bits_denied=pending_bad[pick] && pending_opcode[pick]!=4;
      io_dma_d_bits_corrupt=pending_bad[pick] && pending_opcode[pick]==4;
      io_dma_d_bits_data=0;
      for (response_index=0;response_index<64;response_index=response_index+1)
        io_dma_d_bits_data[response_index*8+:8]=memory[(pending_address[pick] & 32'hffffffc0)-32'h80000000+response_index];
    end
  end
end

task ctrl(input bit write_access, input integer offset, input [63:0] wdata, output [63:0] rdata);
  integer before_count; reg accepted;
  begin
    @(negedge clock); before_count=ctrl_count;
    io_control_a_valid=1; io_control_a_bits_opcode=write_access ? 0 : 4;
    io_control_a_bits_size=3; io_control_a_bits_address=32'h10050000+offset;
    io_control_a_bits_mask=8'hff; io_control_a_bits_data=wdata;
    accepted=0;
    while (!accepted) begin @(posedge clock); accepted=io_control_a_ready; #1; end
    @(negedge clock); io_control_a_valid=0;
    while (ctrl_count==before_count) begin @(posedge clock); #1; end
    rdata=ctrl_result;
  end
endtask

task sp_send(input [2:0] opcode, input integer offset, input integer tag,
             input [511:0] wdata, input [63:0] mask);
  reg accepted;
  begin
    @(negedge clock); io_scratchpad_a_valid=1;
    io_scratchpad_a_bits_opcode=opcode; io_scratchpad_a_bits_size=6;
    io_scratchpad_a_bits_address=32'h70000000+offset;
    io_scratchpad_a_bits_source=tag; io_scratchpad_a_bits_data=wdata;
    io_scratchpad_a_bits_mask=mask; accepted=0;
    while (!accepted) begin @(posedge clock); accepted=io_scratchpad_a_ready; #1; end
    @(negedge clock); io_scratchpad_a_valid=0;
  end
endtask

task sp_access(input [2:0] opcode, input integer offset, input [511:0] wdata,
               input [63:0] mask, output [511:0] rdata);
  integer before_count;
  begin
    before_count=sp_count; sp_send(opcode,offset,100,wdata,mask);
    while (sp_count==before_count) begin @(posedge clock); #1; end
    rdata=sp_result;
  end
endtask

task launch(input bit store_direction, input integer external_offset, input integer local_offset, input integer length);
  reg [63:0] dummy;
  begin
    ctrl(1,8,32'h80000000+external_offset,dummy);
    ctrl(1,16,local_offset,dummy); ctrl(1,24,length,dummy);
    ctrl(1,0,store_direction ? 3 : 1,dummy);
  end
endtask

task finish_dma(input integer expected_error, input integer expected_bytes);
  reg [63:0] status, progress, error_code;
  begin
    status=1;
    while (status[0]) ctrl(0,32,0,status);
    ctrl(0,40,0,progress); ctrl(0,48,0,error_code);
    if (progress!==expected_bytes || error_code!==expected_error || status[2]!=(expected_error!=0))
      $fatal(1,"DMA completion mismatch status=%h bytes=%0d err=%0d expected=%0d/%0d",status,progress,error_code,expected_bytes,expected_error);
    if (expected_error==0 && !status[1]) $fatal(1,"missing DONE");
    checks=checks+1;
  end
endtask

task check_sram_copy(input integer local_offset, input integer external_offset, input integer length, input integer padded_length);
  integer block_index, b; reg [511:0] got, expected;
  begin
    for (block_index=0;block_index<padded_length;block_index=block_index+64) begin
      sp_access(4,local_offset+block_index,0,64'hffffffffffffffff,got);
      for (b=0;b<64;b=b+1)
        expected[b*8+:8]=(block_index+b<length) ? memory[external_offset+block_index+b] : 8'hcc;
      if (got!==expected) $fatal(1,"SRAM copy mismatch local=%h block=%0d",local_offset,block_index);
    end
    checks=checks+1;
  end
endtask

""")
    if desc:
        lines.append(DESC_TASKS)
    lines.append("initial begin")
    lines += initial
    lines.append(r"""
  for (j=0;j<64;j=j+1) pending[j]=0;
  for (j=0;j<65536;j=j+1) memory[j]=(j*13+7) ^ (j>>8);
  io_control_d_ready=1; io_scratchpad_d_ready=1;
  repeat(6) @(negedge clock); reset=0;
  repeat(3) @(negedge clock);
""")
    if desc:
        return "\n".join(lines + [DESC_SCENARIO])
    lines.append(r"""

  // Same globally addressable storage, byte masks and pipelined TL requests.
  for (j=0;j<32;j=j+1) begin
    data_value={64{8'hcc}}; data_value[31:0]=32'h12340000+j;
    sp_access(0,32'h10000+j*64,data_value,64'hffffffffffffffff,expected_value);
  end
  sp_access(1,32'h10000,{64{8'h5a}},64'hf0,data_value);
  sp_access(4,32'h10000,0,64'hffffffffffffffff,data_value);
  if (data_value[63:0]!==64'h5a5a5a5a12340000) $fatal(1,"global byte mask mismatch");
  start_count=sp_count; io_scratchpad_d_ready=0;
  fork
    begin for (integer t=0;t<32;t=t+1) sp_send(4,32'h10000+t*64,t,0,64'hffffffffffffffff); end
    begin repeat(25) @(negedge clock); io_scratchpad_d_ready=1; end
  join
  while (sp_count<start_count+32) begin @(posedge clock); #1; end
  for (j=0;j<32;j=j+1)
    if (sp_results[j][31:0]!==32'h12340000+j) $fatal(1,"global source/order/data mismatch tag=%0d data=%h",j,sp_results[j][31:0]);
  if (max_sp_pending<2) $fatal(1,"global SRAM remains serialized");
  checks=checks+1;
  start_count=sp_count;
  sp_send(0,32'h11000,200,512'h11,64'hffffffffffffffff);
  sp_send(4,32'h11000,201,0,64'hffffffffffffffff);
  sp_send(0,32'h11000,202,512'h22,64'hffffffffffffffff);
  sp_send(4,32'h11000,203,0,64'hffffffffffffffff);
  while (sp_count<start_count+4) begin @(posedge clock); #1; end
  if (sp_results[201]!==512'h11 || sp_results[203]!==512'h22)
    $fatal(1,"global FIFO execution/read-after-write order changed");
  checks=checks+1;

  // More than one full ring: 64 requests outstanding, reversed responses,
  // simultaneous global accesses, busy START rejection, then tag reuse.
  responses_enabled=0; launch(0,0,0,5120);
  while (max_pending<64) begin @(posedge clock); #1; end
  ctrl(0,32,0,value); if (!value[0]) $fatal(1,"BUSY cleared before data returned");
  ctrl(1,0,1,value); ctrl(0,48,0,value);
  if (value!==1) $fatal(1,"busy START changed MMIO semantics");
  ctrl(1,32,4,value); responses_enabled=1;
  for (j=0;j<8;j=j+1) begin
    sp_access(4,32'h10000+j*64,0,64'hffffffffffffffff,data_value);
    if (data_value[31:0]!==32'h12340000+j) $fatal(1,"global/DMA bank arbitration mismatch");
  end
  finish_dma(0,5120); check_sram_copy(0,0,5120,5120);
  responses_enabled=0; launch(1,32'h4000,0,5120);
  while (pending_now<64) begin @(posedge clock); #1; end
  responses_enabled=1; finish_dma(0,5120);
  for (j=0;j<5120;j=j+1) if (memory[32'h4000+j]!==memory[j]) $fatal(1,"DMA STORE mismatch %0d",j);
  checks=checks+1;

  // Aligned fast body followed by a byte-accurate five-byte tail.
  for (j=0;j<3;j=j+1) sp_access(0,32'h4000+j*64,{64{8'hcc}},64'hffffffffffffffff,data_value);
  launch(0,32'h8000,32'h4000,133); finish_dma(0,133);
  check_sram_copy(32'h4000,32'h8000,133,192);
  launch(1,32'ha000,32'h4000,133); finish_dma(0,133);
  for (j=0;j<133;j=j+1) if (memory[32'ha000+j]!==memory[32'h8000+j]) $fatal(1,"STORE tail mismatch");

  // Non-aligned compatibility path and untouched bytes around the copy.
  for (j=0;j<2;j=j+1) sp_access(0,32'h5000+j*64,{64{8'hcc}},64'hffffffffffffffff,data_value);
  launch(0,32'hb003,32'h5005,97); finish_dma(0,97);
  for (j=0;j<2;j=j+1) begin
    sp_access(4,32'h5000+j*64,0,64'hffffffffffffffff,data_value);
    for (k=0;k<64;k=k+1) begin
      if (j*64+k>=5 && j*64+k<102) begin
        if (data_value[k*8+:8]!==memory[32'hb003+j*64+k-5]) $fatal(1,"unaligned LOAD mismatch");
      end else if (data_value[k*8+:8]!==8'hcc) $fatal(1,"unaligned LOAD touched adjacent byte");
    end
  end
  launch(1,32'hc007,32'h5005,97); finish_dma(0,97);
  for (j=0;j<97;j=j+1) if (memory[32'hc007+j]!==memory[32'hb003+j]) $fatal(1,"unaligned STORE mismatch");

  before_dma=dma_count;
  launch(0,0,524256,64); finish_dma(2,0);
  launch(0,0,524224,65); finish_dma(2,0); // Valid 64-B body, invalid tail: reject the whole command.
  ctrl(1,8,32'h90000000,value); ctrl(1,16,0,value); ctrl(1,24,64,value); ctrl(1,0,1,value);
  finish_dma(4,0);
  launch(0,0,0,0); finish_dma(0,0);
  if (dma_count!=before_dma) $fatal(1,"invalid/zero transfer issued external traffic");

  inject_error=1; fault_address=32'h8000e000;
  responses_enabled=0; launch(0,32'he000,32'h6000,4096);
  while (pending_now<64) begin @(posedge clock); #1; end
  responses_enabled=1; finish_dma(5,0);
  fault_address=32'h8000e100;
  responses_enabled=0; launch(1,32'he100,0,4096);
  while (pending_now<64) begin @(posedge clock); #1; end
  responses_enabled=1; finish_dma(5,4032);
  inject_error=0;
  launch(0,32'he000,32'h6000,64); finish_dma(0,64);
  check_sram_copy(32'h6000,32'he000,64,64);
  if (dual_local_cycles==0) $fatal(1,"private DMA lanes never issued concurrently");
  $display("PASS MemTile RTL checks=%0d cycles=%0d max_external_pending=%0d max_global_pending=%0d dual_local_cycles=%0d",checks,cycle,max_pending,max_sp_pending,dual_local_cycles);
  $finish;
end
endmodule
""")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desc", action="store_true", help="Check the DeSC MemTile")
    parser.add_argument("--rtl", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--suite", type=Path, default=Path.home() / "oss-cad-suite")
    args = parser.parse_args()
    args.rtl = args.rtl or (DESC_RTL if args.desc else DEFAULT_RTL)
    suffix = "-desc" if args.desc else ""
    args.output = args.output or ROOT / f"build/xdna2-memtile{suffix}-rtl-check"
    args.output.mkdir(parents=True, exist_ok=True)
    test = args.output / "tb.sv"
    test.write_text(bench(args.rtl))
    executable = args.output / "tb.vvp"
    commands = [
        [str(args.suite / "bin/iverilog"), "-g2012", "-DSYNTHESIS", "-s", "tb", "-o", str(executable),
         "-f", str(args.rtl / "rtl.f"), str(test)],
        [str(args.suite / "bin/vvp"), str(executable)],
    ]
    for name, command in zip(("compile.log", "run.log"), commands):
        with (args.output / name).open("w") as stream:
            subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)
    output = (args.output / "run.log").read_text()
    match = re.search(r"^PASS (?:DeSC )?MemTile RTL (.+)$", output, re.M)
    if not match:
        raise RuntimeError(output)
    result = {name: int(value) for name, value in re.findall(r"(\w+)=(\d+)", match[1])}
    result.update(passed=True, software_tests_run=False, rtl=str(args.rtl), output=str(args.output))
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(output.strip())


if __name__ == "__main__":
    main()
