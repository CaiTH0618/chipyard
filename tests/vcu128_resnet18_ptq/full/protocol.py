import struct
APP_BASE=0x80000000
APP_END=0x8f000000
STACK_BOTTOM=0x8fe00000
STACK_TOP=0x8ff00000
RESULT_BASE=0x8ff00000
RESULT_REGION=4096
LOG_BASE=0x8ff10000
LOG_SIZE=65536
HBM_END=0x90000000
MAGIC=0x52313846554c4c31
BUILD_MAGIC=0x52313846454c4631
PREPARED,RUNNING,DONE,ERROR,WAIT_HOST=1,2,3,4,5
FIELDS='magic run_id state stage hart case_count error cases_done cycles mismatches first_bad output_address output_bytes checksum mcause mepc mtval ack sequence image_end bss_start bss_end log_write'.split()
def parse_header(data):return dict(zip(FIELDS,struct.unpack('<32Q',data[:256])))
def initial_result(run_id,hart,count):
 values=[0]*32;values[:8]=[MAGIC,run_id,PREPARED,0,hart,count,0,0]
 return struct.pack('<32Q',*values)+bytes(RESULT_REGION-256)
def checksum(data):
 h=14695981039346656037
 for byte in data:h=((h^byte)*1099511628211)&0xffffffffffffffff
 return h
