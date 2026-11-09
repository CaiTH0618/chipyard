#!/usr/bin/env python3
"""Build a self-contained Host bundle, only after both ELFs match current data."""
from pathlib import Path
import tarfile
from host_resnet18_full import load_plan
base=Path(__file__).resolve().parent
manifest=base/'generated/full_manifest.json'
for hart in (0,1):
    plan=load_plan(base/f'build/hart{hart}/resnet18_full_hart{hart}.elf',manifest)
    if plan['hart']!=hart:raise ValueError('Wrong hart ELF')
names=['host_resnet18_full.py','protocol.py','xdma_transport.py','requirements.txt','README.md','VALIDATION.md',
       'generated/full_manifest.json','generated/full_blob.bin',
       'build/hart0/resnet18_full_hart0.elf','build/hart1/resnet18_full_hart1.elf']
out=base/'dist/resnet18_full_host.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as archive:
    for name in names:archive.add(base/name,arcname=name,recursive=False)
with tarfile.open(out) as archive:
    if archive.getnames()!=names:raise ValueError('Host bundle members differ')
print('Host bundle generated:',out)
