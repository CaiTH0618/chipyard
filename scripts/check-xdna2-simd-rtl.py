#!/usr/bin/env python3
"""Check the generated SIMD engine and shared SRAM with a directed RTL bench.

No CPU, firmware, software workload or complete Chipyard simulator is used.
"""

import argparse
import json
from pathlib import Path
import re
import struct
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RTL = ROOT / "sims/verilator/generated-src/chipyard.xdna2.Xdna2AreaHarness.Xdna2AIETileConfig"


def ports(path, module):
    header = re.search(rf"\bmodule\s+{module}\s*\((.*?)\);", path.read_text(), re.S)
    if not header:
        raise ValueError(f"Missing {module} interface in {path}")
    found = {}
    for line in header[1].splitlines():
        match = re.match(r"\s*(input|output)\s+(?:\[(\d+):0\]\s+)?(\w+)", line)
        if match:
            direction, high, name = match.groups()
            found[name] = (direction, int(high) + 1 if high else 1)
    if not {"clock", "reset"} <= found.keys():
        raise ValueError(f"Incomplete interface: {module}")
    return found


def testbench(rtl):
    engine = ports(rtl / "gen-collateral/FP32SimdEngine.sv", "FP32SimdEngine")
    memory = ports(rtl / "gen-collateral/AIETileSharedMemory.sv", "AIETileSharedMemory")
    connections = {
        "e_io_scratchpad_request_ready": "m_io_simd_request_ready",
        "e_io_scratchpad_response_valid": "m_io_simd_response_valid",
        "e_io_scratchpad_response_bits": "m_io_simd_response_bits",
        **{f"m_io_simd_request_{s}": f"e_io_scratchpad_request_{s}" for s in (
            "valid", "bits_byteAddress", "bits_write", "bits_data")},
    }
    lines = ["`timescale 1ns/1ps", "module tb;", "reg clock = 0; reg reset = 1;", "always #5 clock = ~clock;"]
    initial = []
    for prefix, module, interface in (("e", "FP32SimdEngine", engine), ("m", "AIETileSharedMemory", memory)):
        pins = []
        for name, (direction, width) in interface.items():
            signal = name if name in ("clock", "reset") else f"{prefix}_{name}"
            pins.append(f".{name}({signal})")
            if name in ("clock", "reset"):
                continue
            kind = "reg" if direction == "input" and signal not in connections else "wire"
            lines.append(f"{kind} [{width-1}:0] {signal};")
            if kind == "reg":
                initial.append(f"{signal} = 0;")
        lines.append(f"{module} {prefix}({', '.join(pins)});")
    lines += [f"assign {dst} = {src};" for dst, src in connections.items()]
    native_banks = [f"m_io_gemmini_spad_{i}" for i in range(4)] + [
        f"m_io_gemmini_acc_{i}_{s}" for i in range(2) for s in range(2)]
    write_cases, stop_write, read_cases, stop_read, check_read = [], [], [], [], []
    for index, name in enumerate(native_banks):
        width = memory[name.removeprefix("m_") + "_read_data"][1]
        write_cases.append(f"{index}: begin {name}_write_en=1; {name}_write_addr=row; {name}_write_data=data; {name}_write_mask=mask; end")
        stop_write.append(f"{index}: {name}_write_en=0;")
        read_cases.append(f"{index}: begin {name}_read_en=1; {name}_read_addr=row; end")
        stop_read.append(f"{index}: {name}_read_en=0;")
        check_read.append(f'{index}: if ({name}_read_data !== expected[{width-1}:0]) $fatal(1,"SRAM mismatch bank=%0d row=%0d", bank,row);')
    vectors = [[float(i + 1) for i in range(32)], [2.0] * 32,
               [1.0 if i % 2 == 0 else -1.0 for i in range(32)]]
    a, b, c = vectors
    vectors += [[x*y+z for x,y,z in zip(a,b,c)], [x*y for x,y in zip(a,b)],
                [x+y for x,y in zip(a,b)], [x-y for x,y in zip(a,b)],
                [2*x*y+z for x,y,z in zip(a,b,c)]]
    words = []
    for vector, values in enumerate(vectors):
        for word in range(4):
            bits = sum(struct.unpack('<I', struct.pack('<f', value))[0] << (lane*32)
                       for lane, value in enumerate(values[word*8:word*8+8]))
            words.append(f"{vector*4+word}: expected_word = 256'h{bits:064x};")
    lines += ["""
integer cycle=0, accepted_count=0, completed_count=0;
integer seen[0:255], accepted[0:255], expected_error[0:255];
integer scenario=0, same_bank_stalls=0;
reg [7:0] blocked_tag;
always @(posedge clock) if (!reset) begin
  cycle = cycle+1;
  if (e_io_control_command_valid && e_io_control_command_ready) begin
    if (accepted[e_io_control_command_bits_tag] >= 0) $fatal(1,"duplicate command tag");
    accepted[e_io_control_command_bits_tag]=cycle;
    accepted_count=accepted_count+1;
  end
  if (e_io_control_completion_valid && e_io_control_completion_ready) begin
    if (accepted[e_io_control_completion_bits_tag] < 0 || seen[e_io_control_completion_bits_tag]) $fatal(1,"unexpected/duplicate completion");
    if (e_io_control_completion_bits_error !== expected_error[e_io_control_completion_bits_tag][0]) $fatal(1,"wrong completion error");
    if (e_io_control_completion_bits_exceptionFlags !== 5'b0) $fatal(1,"unexpected FP exception");
    seen[e_io_control_completion_bits_tag]=1;
    completed_count=completed_count+1;
  end
  if (e_io_scratchpad_request_valid && !e_io_scratchpad_request_ready) begin
    if (scenario==1) same_bank_stalls=same_bank_stalls+1;
    if (scenario==2) $fatal(1,"unrelated bank stalled SIMD");
  end
end
initial begin #200000; $fatal(1,"RTL test timeout"); end
function [255:0] expected_word(input integer vector, input integer word);
  case (vector*4+word)
""", "\n".join(words), "default: expected_word=0; endcase endfunction"]
    lines += ["""
task native_write(input integer bank,input integer row,input [1023:0] data,input [127:0] mask);
  begin case(bank)
""", "\n".join(write_cases), "endcase @(negedge clock); case(bank)",
        "\n".join(stop_write), "endcase end endtask",
        "task native_check(input integer bank,input integer row,input [1023:0] expected); begin case(bank)",
        "\n".join(read_cases), "endcase @(posedge clock); #1; case(bank)",
        "\n".join(check_read), "endcase @(negedge clock); case(bank)",
        "\n".join(stop_read), "endcase end endtask", """
task send(input integer kind,input integer tag,input integer dst,input integer sa,input integer sb,input integer sc,input integer op,input integer addr,input integer rm);
  begin
    e_io_control_command_bits_kind=kind; e_io_control_command_bits_tag=tag;
    e_io_control_command_bits_destination=dst; e_io_control_command_bits_sourceA=sa;
    e_io_control_command_bits_sourceB=sb; e_io_control_command_bits_sourceC=sc;
    e_io_control_command_bits_op=op; e_io_control_command_bits_byteAddress=addr;
    e_io_control_command_bits_roundingMode=rm; e_io_control_command_valid=1;
    do @(posedge clock); while (!e_io_control_command_ready);
    @(negedge clock); e_io_control_command_valid=0;
  end
endtask
task wait_seen(input integer tag); begin while (!seen[tag]) @(negedge clock); end endtask
integer i,j;
initial begin
""", "\n".join(initial), """
  for(i=0;i<256;i=i+1) begin seen[i]=0; accepted[i]=-1; expected_error[i]=0; end
  repeat(5) @(negedge clock); reset=0; e_io_control_completion_ready=1;
  for(j=0;j<4;j=j+1) begin
    native_write(0,j,expected_word(0,j),32'hffffffff);
    native_write(1,j,expected_word(1,j),32'hffffffff);
    native_write(3,j,expected_word(2,j),32'hffffffff);
  end
  // Native byte masks and all private accumulator sub-banks remain functional.
  native_write(0,40,{32{8'ha5}},32'hffffffff);
  native_write(0,40,256'h5a,32'h1);
  native_check(0,40,{{31{8'ha5}},8'h5a});
  for(j=4;j<8;j=j+1) begin
    native_write(j,7,{32{32'h12345678}},128'hffffffffffffffffffffffffffffffff);
    native_check(j,7,{32{32'h12345678}});
  end
  send(0,1,0,0,0,0,0,16'h0000,0); wait_seen(1);
  send(0,2,1,0,0,0,0,16'h4000,0); wait_seen(2);
  send(0,3,2,0,0,0,0,16'hc000,0); wait_seen(3);
  send(2,10,3,0,1,2,0,0,0);
  send(2,11,4,0,1,0,1,0,0);
  send(2,12,5,0,1,0,2,0,0);
  send(2,13,6,0,1,0,3,0,0);
  send(2,14,7,3,4,0,2,0,0); wait_seen(14);
  for(j=11;j<=13;j=j+1) if (accepted[j] != accepted[j-1]+1) $fatal(1,"independent compute throughput below one vector/cycle");
  if (accepted[14] <= accepted[13]+1) $fatal(1,"dependent compute bypassed RAW hazard");
  send(1,20,0,3,0,0,0,16'h8000,0); wait_seen(20);
  send(1,21,0,4,0,0,0,16'h0080,0); wait_seen(21);
  send(1,22,0,5,0,0,0,16'h4080,0); wait_seen(22);
  send(1,23,0,6,0,0,0,16'hc080,0); wait_seen(23);
  send(1,24,0,7,0,0,0,16'h8080,0); wait_seen(24);
  for(j=0;j<4;j=j+1) begin
    native_check(2,j,expected_word(3,j));
    native_check(0,j+4,expected_word(4,j));
    native_check(1,j+4,expected_word(5,j));
    native_check(3,j+4,expected_word(6,j));
    native_check(2,j+4,expected_word(7,j));
  end
  // Gemmini traffic on the selected bank must stall SIMD without losing data.
  send(0,50,0,0,0,0,0,0,0); scenario=1;
  m_io_gemmini_spad_0_write_en=1; m_io_gemmini_spad_0_write_addr=300;
  m_io_gemmini_spad_0_write_data=256'h1234; m_io_gemmini_spad_0_write_mask=32'hffffffff;
  repeat(6) @(negedge clock);
  m_io_gemmini_spad_0_write_en=0; scenario=0; wait_seen(50);
  if (same_bank_stalls < 6) $fatal(1,"missing selected-bank arbitration");
  native_check(0,300,256'h1234);
  // A different busy bank must not block the selected bank.
  send(0,51,0,0,0,0,0,0,0); scenario=2;
  m_io_gemmini_spad_1_write_en=1; m_io_gemmini_spad_1_write_addr=301;
  m_io_gemmini_spad_1_write_data=256'h5678; m_io_gemmini_spad_1_write_mask=32'hffffffff;
  wait_seen(51); m_io_gemmini_spad_1_write_en=0; scenario=0;
  send(1,52,0,0,0,0,0,16'h8100,0); wait_seen(52);
  for(j=0;j<4;j=j+1) native_check(2,j+8,expected_word(0,j));
  // Fill the completion queue while reusing a destination (WAW scoreboard).
  e_io_control_completion_ready=0;
  for(j=100;j<108;j=j+1) send(2,j,7,0,1,0,2,0,0);
  for(j=101;j<108;j=j+1) if (accepted[j] < accepted[j-1]+4) $fatal(1,"WAW destination hazard did not stall issue");
  repeat(10) @(negedge clock);
  if (!e_io_control_busy || e_io_control_command_ready || !e_io_control_completion_valid) $fatal(1,"completion credit accounting failed");
  blocked_tag=e_io_control_completion_bits_tag;
  repeat(8) begin
    @(negedge clock);
    if (!e_io_control_completion_valid || e_io_control_completion_bits_tag!==blocked_tag) $fatal(1,"completion changed under backpressure");
  end
  e_io_control_completion_ready=1; wait_seen(107);
  send(1,110,0,7,0,0,0,16'h8180,0); wait_seen(110);
  for(j=0;j<4;j=j+1) native_check(2,j+12,expected_word(5,j));
  expected_error[120]=1; expected_error[121]=1; expected_error[122]=1;
  send(3,120,0,0,0,0,0,0,0); wait_seen(120);
  send(0,121,0,0,0,0,0,32,0); wait_seen(121);
  send(2,122,0,0,1,2,0,0,7); wait_seen(122);
  send(1,123,0,0,0,0,0,16'h8200,0); wait_seen(123);
  for(j=0;j<4;j=j+1) native_check(2,j+16,expected_word(0,j));
  @(negedge clock);
  if (e_io_control_busy || accepted_count!=completed_count) $fatal(1,"commands/completions did not drain");
  $display("SIMD_INTEGRATION_PASS commands=%0d cycles=%0d",completed_count,cycle);
  $finish;
end
endmodule
"""]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl", type=Path, default=DEFAULT_RTL)
    parser.add_argument("--output", type=Path, default=ROOT / "build/xdna2-simd-rtl-check")
    parser.add_argument("--iverilog", type=Path, default=Path.home() / "oss-cad-suite/bin/iverilog")
    parser.add_argument("--vvp", type=Path, default=Path.home() / "oss-cad-suite/bin/vvp")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    resources = json.loads((args.rtl / "resources.json").read_text())
    if not resources.get("simd_scratchpad_connected"):
        parser.error("Generate the connected AIETile RTL first")
    bench = args.output / "tb.sv"
    bench.write_text(testbench(args.rtl))
    binary = args.output / "sim.vvp"
    commands = ([str(args.iverilog), "-g2012", "-DSYNTHESIS", "-s", "tb", "-o", str(binary),
                 "-f", str(args.rtl / "rtl.f"), str(bench)], [str(args.vvp), str(binary)])
    for command, name in zip(commands, ("compile.log", "simulation.log")):
        with (args.output / name).open("w") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        if result.returncode:
            raise RuntimeError(f"RTL check failed; see {args.output / name}\n" + (args.output / name).read_text()[-4000:])
    output = (args.output / "simulation.log").read_text()
    if "SIMD_INTEGRATION_PASS" not in output:
        raise RuntimeError(f"RTL check did not finish: {output}")
    summary = {"passed": True, "software_workload_run": False, "rtl_directory": str(args.rtl),
               "checks": ["native SRAM ports and masks", "all accumulator sub-banks", "four-beat vector loads/stores",
                          "32-lane FP32 FMA/MUL/ADD/SUB and register writeback", "one independent vector/cycle",
                          "RAW/WAW hazards", "Gemmini priority on a shared bank", "parallel access to different banks",
                          "completion backpressure and credit exhaustion", "invalid commands have no data side effects"],
               "result": output.strip()}
    (args.output / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(output.strip())
    print(f"RTL check results: {args.output / 'result.json'}")


if __name__ == "__main__":
    main()
