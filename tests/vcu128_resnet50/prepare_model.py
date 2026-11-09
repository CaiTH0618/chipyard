#!/usr/bin/env python3
"""Freeze local Gemmini inputs and instrument the supplied ResNet50 revision.

No downloads and no edits to generators/gemmini. Weights/images stay local;
they are copied into this mode's build/inputs before compilation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

SUPPORTED = {
    'imagenet/resnet50.c': '2142608f26457e4fba32b44574eaeffd5f154f5d659a94b24956490b706255fb',
    'include/gemmini.h': '99155f90b13bc6434a756dd5d0a021c3f54517b0cccd059c94b3d784a9d4cf0f',
    'include/gemmini_nn.h': '881078997fd9abbd6bec8cac2451830ffadd458c504cf417ebac0785e394a30f',
}


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def transform(source):
    start=re.search(r'int main\s*\(int argc, char \* argv\[\]\)\s*\{',source)
    boundary=source.find('    uint64_t start, end;', start.end() if start else 0)
    if not start or boundary < 0: raise ValueError('unsupported model entry')
    source=source[:start.start()]+'''int resnet50_infer(void) {
    gemmini_flush(0);
    const enum tiled_matmul_type_t tiled_matmul_type = WS;
    const bool conv = true;
    const bool check = false;
    platform_model_begin();
'''+source[boundary:]
    source='#include "platform.h"\n'+source
    source=source.replace('    // conv_2\n','''    if (platform_stop_after_first()) {
        gemmini_fence();
        __asm__ volatile("fence iorw, iorw" ::: "memory");
        platform_first_output(conv_1_out_pooled, sizeof(conv_1_out_pooled));
        return 0;
    }
    // conv_2
''',1)
    # Emit a stage before every layer without changing its arithmetic.
    source,n=re.subn(r'(?m)^(    // (?:conv|fc)_(\d+)\s*)$',
                     lambda m:'    platform_stage(100 + '+m[2]+');\n'+m[1],source)
    if n!=54: raise ValueError('expected 54 layer markers, got '+str(n))
    marker='    // Find highest probs\n'
    if source.count(marker)!=1: raise ValueError('classification marker mismatch')
    source=source.replace(marker,'''    gemmini_fence();
    __asm__ volatile("fence iorw, iorw" ::: "memory");
    platform_final_output(fc_54_out, fc_54_params.batch_size, fc_54_params.out_features);
'''+marker)
    source,n=re.subn(r'(    uint64_t total_cycles = [^;]+;)',
                     r'\1\n    platform_operator_cycles(total_cycles);',source)
    if n!=1: raise ValueError('cycle accounting marker mismatch')
    source=source.replace('max_idx, max_prob);','(unsigned)max_idx, (int)max_prob);')
    return source


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--gemmini-sw',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(); root=a.gemmini_sw.resolve(); out=a.out.resolve()
    required=list(SUPPORTED)+[
        'include/gemmini_params.h','include/gemmini_testutils.h','include/gemmini_counter.h',
        'imagenet/resnet50_params.h','imagenet/images.h']
    for name in required:
        if not (root/name).is_file(): raise ValueError('missing local input: '+str(root/name))
    hashes={name:digest(root/name) for name in required}
    for name,expected in SUPPORTED.items():
        if hashes[name]!=expected:
            raise ValueError(name+' differs from the supplied revision; do not auto-patch an unknown model/API')
    params=(root/'include/gemmini_params.h').read_text()
    for name,value in [('DIM',8),('XCUSTOM_ACC',3),('BANK_NUM',4),('BANK_ROWS',2048),('ACC_ROWS',1024)]:
        if not re.search(r'^#define\s+'+name+r'\s+'+str(value)+r'\s*$',params,re.M):
            raise ValueError('hardware header mismatch: '+name)
    if not re.search(r'typedef\s+int8_t\s+elem_t\s*;',params): raise ValueError('elem_t must be int8_t')
    if not re.search(r'typedef\s+int32_t\s+acc_t\s*;',params): raise ValueError('acc_t must be int32_t')
    weights=(root/'imagenet/resnet50_params.h').read_text()
    images=(root/'imagenet/images.h').read_text()
    if not re.search(r'images\s*\[4\]\s*\[224\]\s*\[224\]\s*\[3\]',images):
        raise ValueError('expected images[4][224][224][3]')
    if not re.search(r'fc_54_out\s*\[4\]\s*\[1000\]',weights):
        raise ValueError('expected fc_54_out[4][1000]')
    original=(root/'imagenet/resnet50.c').read_text()
    if not re.search(r'int correct\[\]\s*=\s*\{75,\s*900,\s*641,\s*897\}',original):
        raise ValueError('unexpected golden class order')
    generated=transform(original)
    out.mkdir(parents=True,exist_ok=True)
    for name in required:
        destination=out/name if name.startswith('include/') else out/Path(name).name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/name,destination)
        if digest(destination)!=hashes[name]: raise ValueError('input changed while copying: '+name)
    compat=Path(__file__).resolve().parent/'compat/rocc-software/src/xcustom.h'
    dest=out/'rocc-software/src/xcustom.h'; dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(compat,dest)
    (out/'resnet50_model.c').write_text(generated)
    (out/'input_hashes.json').write_text(json.dumps({'source_root':str(root),'sha256':hashes,
        'rocc_compat_sha256':digest(compat),'instrumented_model_sha256':digest(out/'resnet50_model.c')},indent=2)+'\n')
    print('Prepared frozen local model inputs:',out)


if __name__=='__main__':
    try: main()
    except (OSError,ValueError) as e:
        print('PREPARE FAILED:',e,file=sys.stderr);sys.exit(1)
