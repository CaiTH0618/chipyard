#!/usr/bin/env python3
from pathlib import Path
import tarfile
from host_resnet18_dual import load_plan
base=Path(__file__).resolve().parent
load_plan(base/'build/resnet18_dual.elf',base/'generated/dual_manifest.json')
names=['host_resnet18_dual.py','protocol.py','graph_validation.py','xdma_transport.py','compare_runs.py','requirements.txt','README.md','VALIDATION.md','build/resnet18_dual.elf','generated/dual_manifest.json','generated/dual_blob.bin']
out=base/'dist/resnet18_dual_host.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as t:
    for name in names:t.add(base/name,arcname=name,recursive=False)
with tarfile.open(out) as t:
    if t.getnames()!=names:raise ValueError('Host bundle members differ')
print('Host bundle generated:',out)
