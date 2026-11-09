#!/usr/bin/env python3
from pathlib import Path
import tarfile
from host_resnet18_qops import load_plan
base=Path(__file__).resolve().parent
manifest=base/'generated/qops_manifest.json'
for hart in (0,1):
 plan=load_plan(base/f'build/hart{hart}/resnet18_qops_hart{hart}.elf',manifest)
 if plan['hart']!=hart:raise RuntimeError('hart metadata mismatch')
names=['host_resnet18_qops.py','protocol.py','xdma_transport.py','requirements.txt','README.md',
       'generated/qops_manifest.json','generated/qops_blob.bin',
       'build/hart0/resnet18_qops_hart0.elf','build/hart1/resnet18_qops_hart1.elf']
out=base/'dist/resnet18_qops_host.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as tf:
 for name in names:tf.add(base/name,arcname=name,recursive=False)
print('Host bundle generated:',out)
