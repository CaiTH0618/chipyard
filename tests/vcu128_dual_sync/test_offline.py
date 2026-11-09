#!/usr/bin/env python3
"""No device access: real C protocol with two pthreads + independent Python checks."""
import contextlib
from dataclasses import replace
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from protocol import *
from host_dual_sync_test import elf_plan,load_elf,guards,address_probe,Plan,Segment

ROOT=Path(__file__).resolve().parent


class Memory:
    def __init__(self,alias=False): self.data={};self.alias=alias
    def key(self,a): return a&0xfffff if self.alias else a
    def write(self,a,data):
        for i,v in enumerate(data): self.data[self.key(a+i)]=v
    def read(self,a,size): return bytes(self.data.get(self.key(a+i),0) for i in range(size))


class Tests(unittest.TestCase):
    def native(self,cfg,option=None,raw=None):
        runner=ROOT/'build/native_runner'
        self.assertTrue(runner.exists(),'run make check to build native_runner')
        with tempfile.TemporaryDirectory() as temp:
            a=Path(temp)/'input';b=Path(temp)/'output'
            a.write_bytes(initial_shared(cfg) if raw is None else raw)
            subprocess.run([str(runner),str(a),str(b)]+([option] if option else []),check=True,timeout=30)
            data=b.read_bytes()
        self.assertEqual(len(data),SHARED_BYTES+2*MAX_PAYLOAD)
        return data[:SHARED_BYTES],data[SHARED_BYTES:SHARED_BYTES+MAX_PAYLOAD],data[SHARED_BYTES+MAX_PAYLOAD:]

    def small(self,**changes):
        return replace(Config(0x123456789abcdef0,ping_rounds=257,block_rounds=6,barrier_rounds=123,
                              atomic_iters=12345,wait_cycles=5000000000,delay_cycles=1000),**changes)

    def test_all_default_workload(self):
        cfg=replace(Config(0x989abcdef0123456),wait_cycles=5000000000)
        verify_snapshot(*self.native(cfg),cfg)

    def test_individual_masks_and_scheduling(self):
        for mask in (1,2,4,8,3,15):
            with self.subTest(mask=mask):
                cfg=self.small(test_mask=mask,seed=mask*0x1020304050607)
                verify_snapshot(*self.native(cfg,'late0' if mask&1 else 'late1'),cfg)

    def test_barrier_reuse_many_rounds(self):
        cfg=self.small(test_mask=4,barrier_rounds=10000,delay_cycles=5000)
        verify_snapshot(*self.native(cfg),cfg)

    def test_single_round_boundaries(self):
        cfg=self.small(ping_rounds=1,block_rounds=1,barrier_rounds=1,atomic_iters=1,delay_cycles=0)
        verify_snapshot(*self.native(cfg),cfg)

    def test_missing_peer_timeout(self):
        cfg=self.small(wait_cycles=10000000)
        shared,_,_=self.native(cfg,'missing-peer')
        status=parse_status(shared[STATUS_OFFSET:STATUS_OFFSET+STATUS_BYTES])
        self.assertEqual((status['state'],status['error']),(ERROR,2))

    def test_invalid_config_target(self):
        cfg=self.small();raw=bytearray(initial_shared(cfg));struct.pack_into('<Q',raw,0,0)
        shared,_,_=self.native(cfg,raw=raw)
        for h in range(2):
            s=parse_status(shared[STATUS_OFFSET+h*STATUS_BYTES:STATUS_OFFSET+(h+1)*STATUS_BYTES])
            self.assertEqual((s['state'],s['error']),(ERROR,1))

    def test_target_rejects_wrong_counter_initial_value(self):
        cfg=self.small(test_mask=8);raw=bytearray(initial_shared(cfg))
        struct.pack_into('<Q',raw,0x1c0,1)
        shared,_,_=self.native(cfg,raw=raw)
        statuses=[parse_status(shared[STATUS_OFFSET+h*STATUS_BYTES:STATUS_OFFSET+(h+1)*STATUS_BYTES]) for h in range(2)]
        self.assertTrue(any(s['error']==6 for s in statuses))
        self.assertTrue(all(s['state']==ERROR for s in statuses))

    def test_host_rejects_corrupt_stale_incomplete(self):
        cfg=self.small();shared,request,response=self.native(cfg)
        verify_snapshot(shared,request,response,cfg)
        for offset in (16,0x80,0x140,0x1c0,STATUS_OFFSET,STATUS_OFFSET+152,STATUS_OFFSET+240):
            with self.subTest(offset=offset):
                bad=bytearray(shared);bad[offset]^=1
                with self.assertRaises(ValueError): verify_snapshot(bad,request,response,cfg)
        bad=bytearray(response);bad[0]^=1
        with self.assertRaises(ValueError): verify_snapshot(shared,request,bad,cfg)

    def test_host_config_bounds_and_abi(self):
        cfg=self.small()
        self.assertEqual(Config.unpack(cfg.pack()),cfg)
        self.assertEqual(len(initial_shared(cfg)),4096)
        for changes in ({'test_mask':0},{'test_mask':16},{'atomic_iters':0},{'delay_cycles':-1},{'seed':1<<64}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): replace(cfg,**changes).pack()

    def test_probe_guard_and_chunked_load(self):
        memory=Memory()
        with contextlib.redirect_stdout(io.StringIO()): address_probe(memory,99)
        with self.assertRaises(RuntimeError): address_probe(Memory(alias=True),99)
        guards(memory,True);guards(memory,False)
        memory.write(STACK1_BOTTOM,b'X')
        with self.assertRaises(RuntimeError): guards(memory,False)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bytes';payload=bytes(range(256))*300;path.write_bytes(b'PREFIX'+payload)
            plan=Plan(path,[Segment(APP_BASE,6,len(payload),len(payload)+64,5)],{})
            memory=Memory();memory.write(APP_BASE+len(payload),b'Q'*64)
            with contextlib.redirect_stdout(io.StringIO()): load_elf(memory,plan)
            self.assertEqual(memory.read(APP_BASE,len(payload)),payload)
            self.assertEqual(memory.read(APP_BASE+len(payload),64),b'Q'*64) # BSS left for Rocket

    def test_real_elf_if_built(self):
        path=ROOT/'build/dual_sync.elf'
        if not path.exists(): self.skipTest('build RISC-V ELF with make first')
        plan=elf_plan(path);self.assertEqual(plan.symbols['_start'],APP_BASE)
        with tempfile.TemporaryDirectory() as temp:
            bad=Path(temp)/'bad.elf';data=bytearray(path.read_bytes());struct.pack_into('<Q',data,24,APP_BASE+4);bad.write_bytes(data)
            with self.assertRaises(ValueError): elf_plan(bad)
            bad.write_bytes(path.read_bytes()[:80])
            with self.assertRaises(Exception): elf_plan(bad)


if __name__=='__main__': unittest.main(verbosity=2)
