#!/usr/bin/env python3
"""Package the built ELF, frozen model export and matching Host code."""
from pathlib import Path
import tarfile
from bundle import load_plan
root=Path(__file__).resolve().parent
load_plan(root/'build/resnet18_cache_baseline.elf',root/'generated/baseline_manifest.json')
names=['host_resnet18_cache_baseline.py','bundle.py','memory_plan.py','source_graph_validation.py','protocol.py','xdma_transport.py','requirements.txt','README.md','build/resnet18_cache_baseline.elf','generated/baseline_manifest.json','generated/baseline_blob.bin']
out=root/'dist/resnet18_cache_baseline_host.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as t:
    for n in names:t.add(root/n,arcname=n)
print('HOST BUNDLE PASS:',out)
