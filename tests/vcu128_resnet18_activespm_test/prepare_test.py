#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from test_plan import *
SOURCE_FILES=['test.h','runtime.c','reference.c','model_backend.c','resadd_direct.h','start.S','platform.h','platform_runtime.c','linker.ld','test_plan.py','prepare_test.py','hardware_evidence.json']
def prepare(out,max_iterations=4):
    root=Path(__file__).resolve().parent;out=Path(out);cs=cases()
    if not 1<=max_iterations<=32:raise ValueError('max-iterations must be 1..32')
    for c in cs:
        n=output_bytes(c)
        if c['kind']==1:
            if c['offset']!=0 or n!=SPM_BYTES:raise ValueError('Full-RAM test bounds')
        elif not (64<=c['offset'] and c['offset']+n+64<=SPM_BYTES):raise ValueError('SPM case bounds')
    m=dict(abi=1,purpose='ActiveSPM direct-access functional test; not performance data',
        hardware=dict(config='VCU128DualRocketDualGemminiResNet18ActiveSPMConfig',spm_bases=list(SPM_BASES),spm_bytes=SPM_BYTES,control_bases=[0x10050000,0x10051000],control_register_offsets=list(range(0,56,8)),control_access_bytes=8,spm_beat_bytes=8,spm_banks=4,active_spm_dma_started=False),
        max_iterations=max_iterations,case_count=len(cs),cases=cs,buffer_bytes=BUFFER_BYTES,workspace_bytes_per_hart=WORK_BYTES,
        source_sha256={n:sha((root/n).read_bytes()) for n in SOURCE_FILES},
        expected_hashes=[[[fnv(expected(c,seed(h,r,i))) for i,c in enumerate(cs)] for r in range(max_iterations)] for h in (0,1)])
    out.mkdir(parents=True,exist_ok=True);raw=json.dumps(m,indent=2)+'\n';(out/'test_manifest.json').write_text(raw)
    (out/'test_generated.h').write_text(f'#define TEST_COUNT {len(cs)}\n#define TEST_MAX_ITERATIONS {max_iterations}\n#define TEST_BUFFER_BYTES {BUFFER_BYTES}\n#define TEST_WORK_BYTES {WORK_BYTES}\n')
    lines=['#include "test.h"','const uint8_t test_bundle_digest[32] __attribute__((used,section(".rodata.build_info")))={'+','.join(str(x) for x in bytes.fromhex(sha(raw.encode())))+'};','const struct test_case test_cases[]={']
    lines += ['{'+','.join(str(c[k])+'u' for k in FIELDS)+'},' for c in cs];lines.append('};')
    (out/'test_cases.c').write_text('\n'.join(lines)+'\n')
    print(f'ACTIVESPM TEST EXPORT PASS: {len(cs)} cases/hart/iteration; max_iterations={max_iterations}; no model/quantization data required')
    return m
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path('generated'));p.add_argument('--max-iterations',type=int,default=4);a=p.parse_args();prepare(a.out,a.max_iterations)
