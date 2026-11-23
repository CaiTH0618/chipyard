from pathlib import Path
import tarfile
from bundle import load_plan
root=Path(__file__).resolve().parent
load_plan(root/'build/activespm_test.elf',root/'generated/test_manifest.json')
names=['host_activespm_test.py','bundle.py','protocol.py','test_plan.py','xdma_transport.py','requirements.txt','README.md','build/activespm_test.elf','generated/test_manifest.json']
out=root/'dist/activespm_test_host.tar.gz';out.parent.mkdir(exist_ok=True)
with tarfile.open(out,'w:gz') as archive:
    for n in names:archive.add(root/n,arcname=n)
print('HOST BUNDLE PASS:',out)
