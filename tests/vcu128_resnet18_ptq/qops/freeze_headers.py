#!/usr/bin/env python3
import argparse,hashlib,json,re,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--gemmini-sw',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
a=p.parse_args();src=a.gemmini_sw.resolve();out=a.out.resolve()
expected='99155f90b13bc6434a756dd5d0a021c3f54517b0cccd059c94b3d784a9d4cf0f'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
if sha(src/'include/gemmini.h')!=expected:
 raise SystemExit('Gemmini API revision differs from the board-verified snapshot; review signatures before adapting this backend')
t=(src/'include/gemmini_params.h').read_text()
for name,value in [('DIM',8),('BANK_NUM',4),('BANK_ROWS',2048),('ACC_ROWS',1024),('MAX_BYTES',64),('XCUSTOM_ACC',3)]:
 if not re.search(r'^#define\s+'+name+r'\s+'+str(value)+r'\s*$',t,re.M):raise SystemExit('Unexpected hardware header parameter: '+name)
if 'typedef int8_t elem_t;' not in t or 'typedef int32_t acc_t;' not in t:raise SystemExit('Expected INT8/INT32')
if '#define HAS_MVIN_SCALE' not in t or '#define ACC_SCALE_T_IS_FLOAT' not in t:raise SystemExit('Scaling support missing')
if '#define HAS_FIRST_LAYER_OPTIMIZATIONS' in t:raise SystemExit('Expected validated configuration without first-layer optimizations')
files=['include/gemmini.h','include/gemmini_params.h','include/gemmini_counter.h']
for name in files:
 dst=out/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src/name,dst)
x=src/'rocc-software/src/xcustom.h'
if not x.is_file():x=Path(__file__).parent/'compat/rocc-software/src/xcustom.h'
dst=out/'rocc-software/src/xcustom.h';dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(x,dst)
(out/'header_hashes.json').write_text(json.dumps({n:sha(out/n) for n in files+['rocc-software/src/xcustom.h']},indent=2)+'\n')
print('Gemmini API/header snapshot PASS: validated 8x8 INT8 configuration')
