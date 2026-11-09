#!/usr/bin/env python3
"""Check that required atomics/barriers are inlined; no RoCC opcodes emitted."""
import re
from pathlib import Path
import sys

text=Path(sys.argv[1]).read_text()
for mnemonic in ('amoadd.d','fence','mhartid','wfi'):
    if mnemonic not in text: raise SystemExit('missing instruction/CSR: '+mnemonic)
if re.search(r'__atomic_\w+|__sync_\w+',text): raise SystemExit('out-of-line atomic helper found')
for match in re.finditer(r'^\s*[0-9a-f]+:\s+([0-9a-f]{8})\s',text,re.M):
    if int(match[1],16)&127 in (0x0b,0x2b,0x5b,0x7b):
        raise SystemExit('unexpected custom/RoCC instruction')
print('INSTRUCTION CHECK PASS: inline amoadd.d/fence, per-hart entry, no RoCC opcodes')
