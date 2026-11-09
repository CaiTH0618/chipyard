"""Host ABI; configuration is written once before one-shot START."""
import struct
APP_BASE,APP_END,HBM_END=0x80000000,0x8f000000,0x90000000
STACK0_BOTTOM,STACK0_TOP=0x8fc00000,0x8fd00000
STACK1_BOTTOM,STACK1_TOP=0x8fe00000,0x8ff00000
SHARED_BASE,SHARED_BYTES,STATUS_OFFSET=0x8ff00000,4096,0x400
LOG0_BASE,LOG1_BASE,LOG_SIZE=0x8ff10000,0x8ff30000,65536
CONFIG_MAGIC,BUILD_MAGIC,ABI=0x5231384455434631,0x5231384455454c31,1
PREPARED,RUNNING,DONE,ERROR=1,2,3,4
MODES={'serial':0,'dual':1}
STATUS_FIELDS='state hart run_id mode error task_index stage tasks_done local_cycles operator_cycles mcause mepc mtval log_write verified_tasks mismatches bss_start bss_end image_end'.split()
TASK_FIELDS='task_id owner computed verified cycles operator_cycles checksum mismatches first_stage first_byte guard_ok run_id output_address output_bytes'.split()
def config(run_id,mode,swap,count,wait_cycles):
    if not 0<run_id<1<<64 or mode not in (0,1) or swap not in (0,1) or count<2 or not 0<wait_cycles<1<<63:raise ValueError('Invalid config')
    if mode==0 and swap:raise ValueError('serial does not use --swap')
    return struct.pack('<16Q',CONFIG_MAGIC,ABI,run_id,mode,swap,count,wait_cycles,*([0]*9))
def initial_shared(cfg):
    raw=bytearray(SHARED_BYTES);raw[:128]=cfg
    for h in (0,1):struct.pack_into('<4Q',raw,STATUS_OFFSET+256*h,PREPARED,h,struct.unpack_from('<Q',cfg,16)[0],struct.unpack_from('<Q',cfg,24)[0])
    return bytes(raw)
def parse_status(data):return dict(zip(STATUS_FIELDS,struct.unpack('<32Q',data[:256])))
def parse_task(data):return dict(zip(TASK_FIELDS,struct.unpack('<16Q',data[:128])))
def owner(i,mode,swap):return 0 if mode==0 else (i%2)^swap
def checksum(data):
    value=14695981039346656037
    for b in data:value=((value^b)*1099511628211)&((1<<64)-1)
    return value
