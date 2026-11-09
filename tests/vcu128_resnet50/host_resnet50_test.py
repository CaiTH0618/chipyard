#!/usr/bin/env python3
"""Load ResNet50 ELF through the existing XDMA -> FBus path; no software reset.

--dry-run validates the ELF without device access. --probe-only checks the
256 MiB address window while the one-shot START is unused. Device addresses
remain CPU/FBus addresses, matching the supplied successful Host test.
"""
import argparse
import ctypes
from dataclasses import dataclass
import errno
import math
import mmap
import os
from pathlib import Path
import secrets
import struct
import sys
import time

from protocol import *


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
    mode:int
    segments:list
    symbols:dict


def elf_plan(path):
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        raise RuntimeError('pyelftools required: python3 -m pip install pyelftools')
    path=Path(path).resolve(); length=path.stat().st_size
    with path.open('rb') as source:
        elf=ELFFile(source)
        if elf.elfclass!=64 or not elf.little_endian or elf['e_machine']!='EM_RISCV':
            raise ValueError('expected little-endian RV64 ELF')
        if elf['e_type']!='ET_EXEC' or int(elf['e_entry'])!=APP_BASE:
            raise ValueError('expected ET_EXEC with entry 0x80000000')
        if int(elf['e_flags'])&6 != 4: raise ValueError('ELF must use double-float ABI (lp64d)')
        segments=[]
        for segment in elf.iter_segments():
            if segment['p_type']!='PT_LOAD' or not segment['p_memsz']: continue
            a=int(segment['p_paddr']); offset=int(segment['p_offset'])
            fs=int(segment['p_filesz']); ms=int(segment['p_memsz']); flags=int(segment['p_flags'])
            if not (0<=fs<=ms and APP_BASE<=a<a+ms<=APP_END and int(segment['p_vaddr'])==a):
                raise ValueError('PT_LOAD is outside APP or physical/virtual address differs')
            if not (0<=offset<=length and offset+fs<=length): raise ValueError('truncated segment in ELF')
            if any(a<s.address+s.memsz and s.address<a+ms for s in segments):
                raise ValueError('overlapping PT_LOAD segments')
            segments.append(Segment(a,offset,fs,ms,flags))
        if not any(s.address<=APP_BASE< s.address+s.filesz and s.flags&1 for s in segments):
            raise ValueError('entry is not in file-backed executable segment')
        table=elf.get_section_by_name('.symtab')
        if table is None: raise ValueError('unstripped ELF with .symtab required')
        def symbol(name):
            matches=table.get_symbol_by_name(name) or []
            matches=[s for s in matches if s['st_shndx']!='SHN_UNDEF']
            if len(matches)!=1: raise ValueError('missing/ambiguous ELF symbol: '+name)
            return int(matches[0]['st_value'])
        symbols={n:symbol(n) for n in ('_start','__image_end','__bss_start','__bss_end','_stack_bottom','_stack_top')}
        if symbols['_start']!=APP_BASE or (symbols['_stack_bottom'],symbols['_stack_top'])!=(STACK_BOTTOM,STACK_TOP):
            raise ValueError('startup/stack symbols mismatch')
        if not (APP_BASE<=symbols['__bss_start']<=symbols['__bss_end']==symbols['__image_end']<=APP_END):
            raise ValueError('BSS/image symbols mismatch')
        if max(s.address+s.memsz for s in segments)>symbols['__image_end']:
            raise ValueError('segment extends beyond linked image end')
        metadata=symbol('resnet50_build_info')
        for s in segments:
            if s.address<=metadata and metadata+160<=s.address+s.filesz:
                source.seek(s.offset+metadata-s.address)
                info=struct.unpack('<20Q',source.read(160)); break
        else: raise ValueError('build metadata not in file-backed segment')
        mode=info[2]
        expected=(BUILD_MAGIC,ABI,mode,APP_BASE,APP_END,STACK_BOTTOM,STACK_TOP,
                  RESULT_BASE,RESULT_REGION,LOG_BASE,LOG_SIZE,8,8,32,4,1000,256,4000,RESULT_BYTES,1)
        if mode not in MODE_NAMES or info!=expected: raise ValueError('ResNet50 build metadata mismatch')
        return Plan(path,mode,sorted(segments,key=lambda s:s.address),symbols)


class DMA:
    def __init__(self,h2c,c2h):
        self.h2c,self.c2h=h2c,c2h
        self.libc=ctypes.CDLL(None,use_errno=True)
        for name in ('pread','pwrite'):
            fn=getattr(self.libc,name)
            fn.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int64]
            fn.restype=ctypes.c_ssize_t

    def transfer(self,address,data=None,length=None):
        size=len(data) if data is not None else length
        if size is None or size<=0 or not APP_BASE<=address<address+size<=HBM_END:
            raise ValueError('invalid CPU/FBus DMA range')
        output=bytearray(); position=0
        while position<size:
            count=min(65536,size-position); off=(address+position)%4096
            with mmap.mmap(-1,((count+off+4095)//4096)*4096) as buffer:
                anchor=ctypes.c_char.from_buffer(buffer)
                pointer=ctypes.addressof(anchor)+off
                if data is not None: buffer[off:off+count]=data[position:position+count]
                fn=self.libc.pwrite if data is not None else self.libc.pread
                fd=self.h2c if data is not None else self.c2h
                while True:
                    ret=fn(fd,pointer,count,address+position)
                    if ret<0 and ctypes.get_errno()==errno.EINTR: continue
                    break
                del anchor
                if ret<0:
                    error=ctypes.get_errno(); raise OSError(error,os.strerror(error))
                if ret!=count: raise IOError(f'short XDMA transfer at {address+position:#x}: {ret}/{count}')
                if data is None: output.extend(buffer[off:off+count])
            position+=count
        return bytes(output) if data is None else None

    def read(self,address,size): return self.transfer(address,length=size)
    def write(self,address,data): self.transfer(address,data=data)


def compare(actual,expected,label):
    if actual!=expected:
        i=next((i for i,(a,b) in enumerate(zip(actual,expected)) if a!=b),min(len(actual),len(expected)))
        raise RuntimeError(f'{label}: byte {i} mismatch')


def address_probe(dma,run_id):
    addresses=[0x80000000,0x84000000,0x88000000,0x8c000000,
               APP_END,STACK_BOTTOM,RESULT_BASE,HBM_END-64]
    patterns=[bytes(((i*37+j*13+(run_id&255))&255) for j in range(64)) for i in range(len(addresses))]
    # Write all patterns before any comparison, so address aliases are exposed.
    for address,pattern in zip(addresses,patterns): dma.write(address,pattern)
    for address,pattern in zip(addresses,patterns):
        compare(dma.read(address,64),pattern,f'address probe {address:#x}')
    print('Address probe PASS: low/high addresses and alias checks',flush=True)


def load_elf(dma,plan,verify=True,zero_bss=False):
    with plan.path.open('rb') as source:
        for s in plan.segments:
            source.seek(s.offset)
            position=0
            while position<s.filesz:
                data=source.read(min(65536,s.filesz-position))
                if not data: raise IOError('ELF changed/truncated while loading')
                dma.write(s.address+position,data)
                if verify: compare(dma.read(s.address+position,len(data)),data,'ELF readback')
                position+=len(data)
            if zero_bss:
                while position<s.memsz:
                    data=bytes(min(65536,s.memsz-position))
                    dma.write(s.address+position,data)
                    if verify: compare(dma.read(s.address+position,len(data)),data,'BSS readback')
                    position+=len(data)
            print(f'Loaded {s.address:#x}: file={s.filesz:,} memory={s.memsz:,}',flush=True)


def guards(dma,install):
    for number,address in enumerate((APP_END,STACK_BOTTOM,RESULT_BASE+RESULT_REGION,LOG_BASE-64,LOG_BASE+LOG_SIZE)):
        pattern=bytes([0xc1+number])*64
        if install: dma.write(address,pattern)
        compare(dma.read(address,64),pattern,f'guard {address:#x}')


class LogReader:
    def __init__(self): self.position=0
    def update(self,dma,total):
        if total<self.position: return  # transient header while target initializes
        if total-self.position>LOG_SIZE:
            print('\n[target log wrapped; earlier bytes omitted]')
            self.position=total-LOG_SIZE
        data=bytearray()
        while self.position<total:
            index=self.position%LOG_SIZE
            count=min(total-self.position,LOG_SIZE-index)
            data.extend(dma.read(LOG_BASE+index,count)); self.position+=count
        if data: print(data.decode('utf-8',errors='replace'),end='',flush=True)


def run_board(args,plan):
    fds=[]
    try:
        for path,flags in ((args.h2c,os.O_RDWR),(args.c2h,os.O_RDWR),(args.user,os.O_RDWR|os.O_SYNC)):
            fds.append(os.open(path,flags))
        dma=DMA(fds[0],fds[1]); run_id=secrets.randbits(64) or 1
        with mmap.mmap(fds[2],4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE) as bar:
            def reg(offset): return struct.unpack_from('<I',bar,offset)[0]
            if reg(0)!=0x534f4301: raise RuntimeError('wrong BAR controller ID')
            deadline=time.monotonic()+10
            while not reg(8)&2:
                if time.monotonic()>=deadline: raise TimeoutError('infrastructure not ready')
                time.sleep(.1)
            if reg(8)&1: raise RuntimeError('one-shot START already used: perform your verified full-path reset first')
            address_probe(dma,run_id)
            if args.probe_only:
                print('PROBE ONLY PASS: START not issued'); return
            load_elf(dma,plan,verify=not args.no_verify_load,zero_bss=args.zero_bss)
            prepared=initial_result(run_id,plan.mode)
            dma.write(RESULT_BASE,prepared)
            compare(dma.read(RESULT_BASE,RESULT_REGION),prepared,'result initialization')
            dma.write(LOG_BASE,bytes(LOG_SIZE)); guards(dma,True)
            struct.pack_into('<I',bar,0x0c,1)
            if not reg(8)&1: raise RuntimeError('START not accepted')
            start=time.monotonic(); last=None; reader=LogReader()
            while True:
                header=parse_header(dma.read(RESULT_BASE,HEADER_BYTES))
                # Runtime briefly clears its header. Only terminal states require full identity.
                if header['magic']==MAGIC and header['run_id']==run_id:
                    reader.update(dma,header['log_write'])
                    state=(header['state'],header['stage'])
                    if state!=last:
                        print(f'Host: state={state[0]} stage={state[1]} elapsed={time.monotonic()-start:.1f}s',flush=True)
                        last=state
                    if header['state']==ERROR:
                        raise RuntimeError(f'target ERROR code={header["error"]} stage={header["stage"]} '
                                           f'mcause={header["mcause"]:#x} mepc={header["mepc"]:#x} mtval={header["mtval"]:#x}')
                    if header['state']==DONE: break
                if time.monotonic()-start>=args.timeout_sec:
                    raise TimeoutError('model did not finish; last header='+repr(header))
                time.sleep(args.poll_sec)
            header=parse_header(dma.read(RESULT_BASE,HEADER_BYTES))
            outputs=dma.read(RESULT_BASE+HEADER_BYTES,OUTPUT_BYTES) if plan.mode==2 else b''
            reader.update(dma,header['log_write'])
            verify_final(header,outputs,plan.mode,run_id); guards(dma,False)
            for name in ('__image_end','__bss_start','__bss_end'):
                field={'__image_end':'image_end','__bss_start':'bss_start','__bss_end':'bss_end'}[name]
                if header[field]!=plan.symbols[name]: raise RuntimeError('runtime linker symbol mismatch: '+name)
            if plan.mode==2:
                for b in range(4): print(f'Image {b}: class={header["predictions"][b]} score={header["scores"][b]} expected={EXPECTED[b]}')
                print('PASS FULL RESNET50: Host rechecked 4x1000 outputs, golden rule, checksum and guards')
            elif plan.mode==1:
                print(f'PASS FIRST-LAYER EXECUTION: bytes={header["first_bytes"]} checksum={header["checksum"]:#x}; no numerical golden comparison in this mode')
            else: print('PASS INIT ONLY: full linked image/BSS initialized; no model execution')
            print(f'Model interval cycles={header["cycles_total"]}; sum of operator intervals={header["cycles_ops"]}')
    finally:
        for fd in fds: os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path)
    p.add_argument('--h2c',default='/dev/xdma0_h2c_0')
    p.add_argument('--c2h',default='/dev/xdma0_c2h_0')
    p.add_argument('--user',default='/dev/xdma0_user')
    p.add_argument('--timeout-sec',type=float,default=1800)
    p.add_argument('--poll-sec',type=float,default=.25)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--probe-only',action='store_true')
    p.add_argument('--no-verify-load',action='store_true')
    p.add_argument('--zero-bss',action='store_true',help='also zero via Host; target still performs its own BSS initialization')
    args=p.parse_args()
    if not all(math.isfinite(v) and v>0 for v in (args.timeout_sec,args.poll_sec)):
        p.error('timeouts must be positive finite values')
    if args.probe_only and (args.elf or args.dry_run): p.error('--probe-only is used without --elf/--dry-run')
    if args.probe_only: return run_board(args,None)
    if args.elf is None: p.error('--elf is required except in --probe-only mode')
    plan=elf_plan(args.elf)
    print(f'ELF mode={MODE_NAMES[plan.mode]} entry={APP_BASE:#x} image_end={plan.symbols["__image_end"]:#x}')
    for s in plan.segments:
        print(f'PT_LOAD {s.address:#x}..{s.address+s.memsz:#x} file={s.filesz:,} memory={s.memsz:,}')
    if args.dry_run:
        print('DRY RUN PASS: ELF ABI, layout and symbols checked; no device access');return
    return run_board(args,plan)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('FAIL:',error,file=sys.stderr);sys.exit(1)
