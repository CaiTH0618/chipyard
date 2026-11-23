import struct
APP_BASE,APP_END,HBM_END=0x80000000,0x8f000000,0x90000000
STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP=0x8fc00000,0x8fd00000,0x8fe00000,0x8ff00000
SHARED_BASE,SHARED_BYTES,STATUS_OFFSET=0x8ff00000,4096,0x400
LOG0_BASE,LOG1_BASE,LOG_SIZE=0x8ff10000,0x8ff30000,65536
CONFIG_MAGIC,BUILD_MAGIC,ABI=0x4153504d43464731,0x4153504d454c4631,1
PREPARED,RUNNING,DONE,ERROR=1,2,3,4
MODES={'hart0':1,'hart1':2,'dual':3};SUITES={'cpu':0,'all':1}
STATUS_FIELDS='state hart run_id mode error task_index stage tasks_done local_cycles operator_cycles mcause mepc mtval log_write verified_tasks mismatches bss_start bss_end image_end checked_stages iterations_done warmup_done first_bad expected_byte actual_byte'.split()
RECORD_FIELDS='state hart iteration case_index output_address output_bytes actual_hash reference_hash cycles first_bad expected_byte actual_byte guard_ok seed kind reserved'.split()
def config(run_id,mask,suite,iterations,wait_cycles,max_iterations):
    if not 0<run_id<1<<64 or mask not in (1,2,3) or suite not in (0,1) or not 1<=iterations<=max_iterations or not 0<wait_cycles<1<<63:raise ValueError('Invalid test config')
    return struct.pack('<16Q',CONFIG_MAGIC,ABI,run_id,mask,suite,iterations,wait_cycles,*([0]*9))
def initial_shared(cfg):
    raw=bytearray(SHARED_BYTES);raw[:128]=cfg;values=struct.unpack('<16Q',cfg)
    for h in (0,1):struct.pack_into('<4Q',raw,STATUS_OFFSET+256*h,PREPARED,h,values[2],values[3])
    return bytes(raw)
def parse_status(data):return dict(zip(STATUS_FIELDS,struct.unpack('<32Q',data[:256])))
def parse_record(data):return dict(zip(RECORD_FIELDS,struct.unpack('<16Q',data[:128])))
