#!/usr/bin/env python3
"""Package the built ELF, frozen model export and matching Host code."""
from pathlib import Path
import tarfile
from bundle import load_plan
root=Path(__file__).resolve().parent
load_plan(root/'build/resnet18_activespm_weights.elf',root/'generated/baseline_manifest.json')
names=['host_resnet18_activespm_weights.py','compare_weights.py','compare_performance.py','bundle.py','memory_plan.py','weight_plan.py','weight_report.py','source_graph_validation.py','protocol.py','xdma_transport.py','requirements.txt','README.md','build/resnet18_activespm_weights.elf','generated/baseline_manifest.json','generated/baseline_blob.bin','generated/weight_plan.json']
out=root/'dist/resnet18_activespm_weights_host.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as t:
    for n in names:t.add(root/n,arcname=n)
print('HOST BUNDLE PASS:',out)
