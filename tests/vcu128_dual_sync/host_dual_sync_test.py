#!/usr/bin/env python3
"""Load one dual-hart ELF; run ping/block/barrier/atomic through shared memory.

No software reset register is accessed. One START per verified full-path reset.
--dry-run never opens a device. --test selects a runtime mask, not another ELF.
"""
import argparse
from dataclasses import dataclass,asdict
import json
import math
import mmap
import os
from pathlib import Path
import secrets
import struct
import sys
import time
from protocol import *
from xdma_transport import DMA,compare


@dataclass(frozen=True)
class Segment:
    address:int
    offset:int
    filesz:int
    memsz:int
    flags:int


@dataclass
class Plan:
    path:Path
    segments:list
    symbols:dict


def elf_plan(path):
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        raise RuntimeError('install dependency: python3 -m pip install pyelftools')
    path=Path(path).resolve();length=path.stat().st_size
    with path.open('rb') as source:
        elf=ELFFile(source)
        if (elf.elfclass,elf.little_endian,elf['e_machine'],elf['e_type'],int(elf['e_entry']))!=(64,True,'EM_RISCV','ET_EXEC',APP_BASE):
            raise ValueError('expected RV64 little-endian ET_EXEC, entry 0x80000000')
        if int(elf['e_flags'])&6: raise ValueError('expected soft-float lp64 ABI')
        segments=[]
        for s in elf.iter_segments():
            if s['p_type']!='PT_LOAD' or not s['p_memsz']: continue
            a,off,fs,ms,flags=(int(s[k]) for k in ('p_paddr','p_offset','p_filesz','p_memsz','p_flags'))
            if not (0<=fs<=ms and APP_BASE<=a<a+ms<=APP_LIMIT and s['p_vaddr']==a and 0<=off<=off+fs<=length):
                raise ValueError('invalid/truncated PT_LOAD or outside APP')
            if any(a<t.address+t.memsz and t.address<a+ms for t in segments): raise ValueError('overlapping segments')
            segments.append(Segment(a,off,fs,ms,flags))
        if not any(s.address<=APP_BASE<s.address+s.filesz and s.flags&1 for s in segments):
            raise ValueError('entry not in executable file-backed segment')
        symtab=elf.get_section_by_name('.symtab')
        if symtab is None: raise ValueError('unstripped ELF with symbols required')
        undefined=[s.name for s in symtab.iter_symbols() if s.name and s['st_shndx']=='SHN_UNDEF' and s['st_info']['bind']=='STB_GLOBAL']
        if undefined: raise ValueError('undefined symbols: '+', '.join(undefined))
        def symbol(name):
            matches=[s for s in symtab.get_symbol_by_name(name) or [] if s['st_shndx']!='SHN_UNDEF']
            if len(matches)!=1: raise ValueError('missing/ambiguous symbol '+name)
            return int(matches[0]['st_value'])
        names=('_start','__bss_start','__bss_end','__image_end','_stack0_bottom','_stack0_top','_stack1_bottom','_stack1_top')
        symbols={n:symbol(n) for n in names}
        if symbols['_start']!=APP_BASE: raise ValueError('wrong _start')
        if tuple(symbols[n] for n in names[4:])!=(STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP):
            raise ValueError('wrong per-hart stacks')
        if not APP_BASE<=symbols['__bss_start']<=symbols['__bss_end']==symbols['__image_end']<=APP_LIMIT:
            raise ValueError('wrong BSS/image range')
        if max(s.address+s.memsz for s in segments)>symbols['__image_end']: raise ValueError('segment exceeds image end')
        address=symbol('dual_sync_build_info')
        expected=(BUILD_MAGIC,ABI,APP_BASE,APP_LIMIT,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,
                  SHARED_BASE,SHARED_BYTES,REQUEST_BASE,RESPONSE_BASE,MAX_PAYLOAD,128,STATUS_OFFSET,STATUS_BYTES)
        for s in segments:
            if s.address<=address and address+128<=s.address+s.filesz:
                source.seek(s.offset+address-s.address)
                if struct.unpack('<16Q',source.read(128))!=expected: raise ValueError('build metadata mismatch')
                break
        else: raise ValueError('build metadata not file-backed')
    return Plan(path,sorted(segments,key=lambda s:s.address),symbols)


def load_elf(dma,plan):
    with plan.path.open('rb') as source:
        for s in plan.segments:
            source.seek(s.offset);position=0
            while position<s.filesz:
                data=source.read(min(65536,s.filesz-position))
                if not data: raise IOError('ELF changed/truncated during load')
                dma.write(s.address+position,data)
                compare(dma.read(s.address+position,len(data)),data,'ELF readback')
                position+=len(data)
            print(f'Loaded {s.address:#x}: file={s.filesz:,} memory={s.memsz:,}',flush=True)


GUARD_ADDRESSES=(APP_LIMIT,STACK0_BOTTOM,STACK1_BOTTOM,SHARED_BASE-64,SHARED_BASE+SHARED_BYTES,
                 REQUEST_BASE-64,REQUEST_BASE+MAX_PAYLOAD,RESPONSE_BASE-64,RESPONSE_BASE+MAX_PAYLOAD)


def guards(dma,install):
    for i,address in enumerate(GUARD_ADDRESSES):
        data=bytes([0xc0+i])*64
        if install: dma.write(address,data)
        compare(dma.read(address,64),data,f'guard {address:#x}')


def address_probe(dma,run_id):
    addresses=(APP_BASE,SHARED_BASE,REQUEST_BASE,RESPONSE_BASE,RESPONSE_BASE+MAX_PAYLOAD-64)
    patterns=[bytes((i*43+j*17+(run_id&255))&255 for j in range(64)) for i in range(len(addresses))]
    for a,p in zip(addresses,patterns): dma.write(a,p)
    for a,p in zip(addresses,patterns): compare(dma.read(a,64),p,f'address probe {a:#x}')
    print('Address probe PASS: program/shared/request/response ranges',flush=True)


def print_error(hart,s):
    return (f'hart{hart} ERROR {s["error"]} ({ERRORS.get(s["error"],"unknown")}), '
            f'test={TESTS.get(s["error_test"],s["error_test"])} round={s["error_round"]} '
            f'index={s["error_index"]} expected={s["expected"]:#x} observed={s["observed"]:#x}; '
            f'mcause={s["mcause"]:#x} mepc={s["mepc"]:#x} mtval={s["mtval"]:#x}')


def run_board(args,plan,cfg):
    fds=[]
    try:
        for path,flags in ((args.h2c,os.O_RDWR),(args.c2h,os.O_RDWR),(args.user,os.O_RDWR|os.O_SYNC)):
            fds.append(os.open(path,flags))
        dma=DMA(fds[0],fds[1])
        with mmap.mmap(fds[2],4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE) as bar:
            def reg(offset): return struct.unpack_from('<I',bar,offset)[0]
            if reg(0)!=0x534f4301: raise RuntimeError('wrong BAR controller ID')
            deadline=time.monotonic()+10
            while not reg(8)&2:
                if time.monotonic()>=deadline: raise TimeoutError('infrastructure not ready')
                time.sleep(.1)
            if reg(8)&1: raise RuntimeError('START already used: perform your verified full-path reset first')
            address_probe(dma,cfg.run_id);load_elf(dma,plan)
            initial=initial_shared(cfg);dma.write(SHARED_BASE,initial)
            compare(dma.read(SHARED_BASE,SHARED_BYTES),initial,'shared initialization')
            # Poison payloads so stale data/partial writes are more readily exposed.
            dma.write(REQUEST_BASE,bytes([0xa6])*MAX_PAYLOAD)
            dma.write(RESPONSE_BASE,bytes([0x59])*MAX_PAYLOAD)
            guards(dma,True)
            print(f'Config: mask={cfg.test_mask:#x}, ping={cfg.ping_rounds}, block={cfg.block_rounds}, '
                  f'barrier={cfg.barrier_rounds}, atomic={cfg.atomic_iters}/hart, seed={cfg.seed:#x}',flush=True)
            struct.pack_into('<I',bar,0x0c,1)
            if not reg(8)&1: raise RuntimeError('START not accepted')
            begin=time.monotonic();last=[None,None];last_report=begin
            while True:
                raw=dma.read(SHARED_BASE+STATUS_OFFSET,2*STATUS_BYTES)
                statuses=[parse_status(raw[h*STATUS_BYTES:(h+1)*STATUS_BYTES]) for h in range(2)]
                failed=False
                for hart,s in enumerate(statuses):
                    if s['run_id']!=cfg.run_id: raise RuntimeError('unexpected status epoch')
                    if s['state']==ERROR:
                        print(print_error(hart,s),file=sys.stderr,flush=True);failed=True
                    key=(s['state'],s['test'])
                    if key!=last[hart]:
                        print(f'hart{hart}: state={s["state"]} test={TESTS.get(s["test"],s["test"])} '
                              f'round={s["round"]} elapsed={time.monotonic()-begin:.3f}s',flush=True)
                        last[hart]=key
                if failed: raise RuntimeError('target reported an error; no further START issued')
                if all(s['state']==DONE for s in statuses): break
                now=time.monotonic()
                if now-begin>=args.timeout_sec: raise TimeoutError('last per-hart statuses: '+repr(statuses))
                if now-last_report>=2:
                    print('Progress: '+', '.join(f'h{h} {TESTS.get(s["test"],s["test"])}/{s["round"]}' for h,s in enumerate(statuses)),flush=True)
                    last_report=now
                time.sleep(args.poll_sec)
            target_elapsed=time.monotonic()-begin
            shared=dma.read(SHARED_BASE,SHARED_BYTES)
            request=dma.read(REQUEST_BASE,MAX_PAYLOAD);response=dma.read(RESPONSE_BASE,MAX_PAYLOAD)
            print('Both harts DONE; independently checking expected words/digests and final buffers...',flush=True)
            statuses=verify_snapshot(shared,request,response,cfg,plan);guards(dma,False)
            for hart,s in enumerate(statuses):
                print(f'hart{hart}: ping={s["ping_done"]} block={s["block_done"]} barrier={s["barrier_done"]} '
                      f'atomic={s["atomic_done"]} checked_bytes={s["checked_bytes"]:,}')
                print(f'  cycles total={s["cycles_total"]} ping={s["cycles_ping"]} block={s["cycles_block"]} '
                      f'barrier={s["cycles_barrier"]} atomic={s["cycles_atomic"]}')
            for name,mask in list(MASKS.items())[:-1]:
                if cfg.test_mask&mask: print(f'PASS {name.upper()}')
            if cfg.test_mask&8: print(f'Atomic counter verified: {2*cfg.atomic_iters}')
            print('PASS DUAL ROCKET SYNC: both harts, round counts, data digests, final payloads and guards')
            if args.result_json:
                result={'elf':str(plan.path),'config':asdict(cfg),'statuses':statuses,
                        'validation':'passed','target_wait_seconds':target_elapsed}
                args.result_json.write_text(json.dumps(result,indent=2)+'\n')
    finally:
        for fd in fds: os.close(fd)


def integer(s): return int(s,0)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--test',choices=MASKS,default='all')
    p.add_argument('--ping-rounds',type=integer,default=10000)
    p.add_argument('--block-rounds',type=integer,default=96)
    p.add_argument('--barrier-rounds',type=integer,default=1000)
    p.add_argument('--atomic-iters',type=integer,default=100000)
    p.add_argument('--seed',type=integer,default=0x123456789abcdef0)
    p.add_argument('--wait-cycles',type=integer,default=500000000,help='per target wait timeout; 10s at 50MHz')
    p.add_argument('--delay-cycles',type=integer,default=200,help='vary per-hart scheduling; not a correctness barrier')
    p.add_argument('--timeout-sec',type=float,default=120)
    p.add_argument('--poll-sec',type=float,default=.05)
    p.add_argument('--h2c',default='/dev/xdma0_h2c_0')
    p.add_argument('--c2h',default='/dev/xdma0_c2h_0')
    p.add_argument('--user',default='/dev/xdma0_user')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--result-json',type=Path)
    args=p.parse_args()
    if not all(math.isfinite(x) and x>0 for x in (args.timeout_sec,args.poll_sec)): p.error('timeouts must be positive finite')
    cfg=Config(secrets.randbits(64) or 1,args.seed,MASKS[args.test],args.ping_rounds,args.block_rounds,
               args.barrier_rounds,args.atomic_iters,args.wait_cycles,args.delay_cycles).validate()
    plan=elf_plan(args.elf)
    print(f'ELF entry={APP_BASE:#x} image_end={plan.symbols["__image_end"]:#x}; two harts, lp64, no Gemmini instructions')
    for s in plan.segments: print(f'PT_LOAD {s.address:#x}..{s.address+s.memsz:#x} file={s.filesz:,} memory={s.memsz:,}')
    if args.dry_run:
        print('DRY RUN PASS: ELF layout, ABI, symbols and metadata; no device access');return
    run_board(args,plan,cfg)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('FAIL:',error,file=sys.stderr);sys.exit(1)
