"""Full-model ABI 4, selectable HBM/SPM and resident weights."""
import struct
APP_BASE,APP_END,HBM_END=0x80000000,0x8f000000,0x90000000
STACK0_BOTTOM,STACK0_TOP=0x8fc00000,0x8fd00000
STACK1_BOTTOM,STACK1_TOP=0x8fe00000,0x8ff00000
SHARED_BASE,SHARED_BYTES,STATUS_OFFSET=0x8ff00000,4096,0x400
LOG0_BASE,LOG1_BASE,LOG_SIZE=0x8ff10000,0x8ff30000,65536
CONFIG_MAGIC,BUILD_MAGIC,ABI=0x5231385343464734,0x52313853454c4634,4
PREPARED,RUNNING,DONE,ERROR=1,2,3,4
MAX_REPEATS,MAX_WARMUP=32,32
SPM_BASES=(0x70000000,0x70100000);SPM_BYTES=1048576
STORAGES={'hbm':0,'spm':1};HARDWARES={'cacheonly':0,'activespm':1}
MODES={'serial':0,'dual':1,'hart1':2};CHECKS={'debug':0,'perf':1}
WEIGHTS={'none':0,'resident':1}
WEIGHT_FIELDS='copy_cycles verify_cycles setup_cycles bytes entries verified guard_ok resident_base first_bad_entry first_bad_byte expected_byte actual_byte final_verified final_verify_cycles'.split()
STATUS_FIELDS='state hart run_id mode error task_index stage tasks_done local_cycles operator_cycles mcause mepc mtval log_write verified_tasks mismatches bss_start bss_end image_end checked_stages iterations_done warmup_done first_bad expected_byte actual_byte storage work_base work_guard_ok'.split()
TASK_FIELDS='task_id owner computed verified cycles operator_cycles checksum mismatches first_stage first_byte guard_ok run_id output_address output_bytes checked_stages reserved'.split()
def config(run_id,mode,swap,count,wait_cycles,check_mode,warmup,repeats,storage=0,probe_spm=0,weight_mode=0):
    if not 0<run_id<1<<64 or mode not in (0,1,2) or swap not in (0,1) or not 1<=count<=16 or not 0<wait_cycles<1<<63:raise ValueError('Invalid config')
    if mode!=1 and swap:raise ValueError('Only dual uses --swap')
    if check_mode not in (0,1) or not 0<=warmup<=MAX_WARMUP or not 1<=repeats<=MAX_REPEATS:raise ValueError('Invalid check/warmup/repeats')
    if storage not in (0,1) or probe_spm not in (0,1) or storage and not probe_spm:raise ValueError('SPM storage requires ActiveSPM hardware')
    if weight_mode not in (0,1) or weight_mode and storage!=1:raise ValueError('Resident weights require SPM activation storage and ActiveSPM hardware')
    return struct.pack('<16Q',CONFIG_MAGIC,ABI,run_id,mode,swap,count,wait_cycles,check_mode,warmup,repeats,storage,probe_spm,weight_mode,*([0]*3))
def initial_shared(cfg):
    raw=bytearray(SHARED_BYTES);raw[:128]=cfg
    for h in (0,1):struct.pack_into('<4Q',raw,STATUS_OFFSET+256*h,PREPARED,h,struct.unpack_from('<Q',cfg,16)[0],struct.unpack_from('<Q',cfg,24)[0])
    return bytes(raw)
def parse_status(data):return dict(zip(STATUS_FIELDS,struct.unpack('<32Q',data[:256])))
def parse_task(data):return dict(zip(TASK_FIELDS,struct.unpack('<16Q',data[:128])))
def parse_weight_status(data):return dict(zip(WEIGHT_FIELDS,struct.unpack('<16Q',data[:128])))
def owner(i,mode,swap):return 0 if mode==0 else 1 if mode==2 else (i%2)^swap
def checksum(data):
    value=14695981039346656037
    for b in data:value=((value^b)*1099511628211)&((1<<64)-1)
    return value
