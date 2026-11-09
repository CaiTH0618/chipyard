#!/usr/bin/env python3
"""Offline protocol and loader tests; never opens an XDMA device."""
import contextlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from protocol import *
from host_resnet50_test import address_probe, guards, load_elf, Plan, Segment, elf_plan


class Memory:
    def __init__(self, alias=False): self.data={}; self.alias=alias
    def key(self,a): return a & 0x3ffffff if self.alias else a
    def write(self,a,data):
        for i,v in enumerate(data): self.data[self.key(a+i)]=v
    def read(self,a,n): return bytes(self.data.get(self.key(a+i),0) for i in range(n))


class Offline(unittest.TestCase):
    def final(self,mode=2):
        header=parse_header(initial_result(99,mode))
        header.update(state=DONE,stage=400,hart=0,error=0)
        return header

    def valid_full(self):
        outputs=bytearray(4000)
        for b,c in enumerate(EXPECTED): outputs[b*1000+c]=9
        h=self.final(); h.update(flags=3,predictions=EXPECTED,scores=(9,)*4,checksum=fnv64(outputs))
        return h,bytes(outputs)

    def test_protocol_offsets(self):
        self.assertEqual((OFFSETS['mcause'],OFFSETS['log_write'],OFFSETS['checksum']),(64,112,176))
        h=parse_header(initial_result(99,2))
        self.assertEqual((h['run_id'],h['state'],h['expected']),(99,PREPARED,EXPECTED))

    def test_full(self):
        h,out=self.valid_full(); verify_final(h,out,2,99)

    def test_tied_golden(self):
        h,out=self.valid_full(); out=bytearray(out)
        out[0]=9; h['predictions']=(0,900,641,897); h['checksum']=fnv64(out)
        verify_final(h,out,2,99)

    def test_reject_stale_corrupt_wrong_golden(self):
        h,out=self.valid_full()
        with self.assertRaises(ValueError): verify_final(h,out,2,100)
        with self.assertRaises(ValueError): verify_final(h,bytes(4000),2,99)
        bad=bytearray(out); bad[0]=10
        h.update(predictions=(0,900,641,897),scores=(10,9,9,9),checksum=fnv64(bad))
        with self.assertRaises(ValueError): verify_final(h,bad,2,99)

    def test_reject_constant_vector(self):
        out=bytes(4000); h=self.final()
        h.update(flags=3,predictions=(0,)*4,scores=(0,)*4,checksum=fnv64(out))
        with self.assertRaises(ValueError): verify_final(h,out,2,99)

    def test_partial(self):
        verify_final(self.final(0),b'',0,99)
        h=self.final(1);h['first_bytes']=802816
        verify_final(h,b'',1,99)
        h['first_bytes']=0
        with self.assertRaises(ValueError): verify_final(h,b'',1,99)

    def test_probe_alias_and_guards(self):
        memory=Memory()
        with contextlib.redirect_stdout(io.StringIO()): address_probe(memory,12)
        with self.assertRaises(RuntimeError): address_probe(Memory(alias=True),12)
        guards(memory,True);guards(memory,False)
        memory.write(STACK_BOTTOM,b'X')
        with self.assertRaises(RuntimeError): guards(memory,False)

    def test_stream_loader(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.bin'; payload=bytes(range(256))*300
            path.write_bytes(b'HEADER'+payload)
            plan=Plan(path,0,[Segment(APP_BASE,6,len(payload),len(payload)+128,5)],{})
            memory=Memory(); memory.write(APP_BASE+len(payload),b'X'*128)
            with contextlib.redirect_stdout(io.StringIO()): load_elf(memory,plan,True,True)
            self.assertEqual(memory.read(APP_BASE,len(payload)+128),payload+bytes(128))


if __name__=='__main__':
    # Optional real ELF path verifies its metadata, loading and rejection of a wrong entry.
    if len(sys.argv)==2:
        plan=elf_plan(sys.argv.pop())
        with tempfile.TemporaryDirectory() as temp:
            bad=Path(temp)/'wrong-entry.elf'; data=bytearray(plan.path.read_bytes())
            struct.pack_into('<Q',data,24,APP_BASE+4);bad.write_bytes(data)
            try: elf_plan(bad)
            except ValueError: pass
            else: raise AssertionError('wrong ELF entry accepted')
        print('Real ELF metadata and invalid-entry rejection PASS')
    unittest.main()
