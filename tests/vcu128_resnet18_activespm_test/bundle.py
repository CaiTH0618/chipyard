import json,struct
from pathlib import Path
from protocol import *
from test_plan import FIELDS,SPM_BASES,SPM_BYTES,BUFFER_BYTES,WORK_BYTES,sha,fnv,cases,output_bytes,seed,expected
def build_info(m):
    n=m['case_count'];r=m['max_iterations']
    return (BUILD_MAGIC,ABI,APP_BASE,APP_END,STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP,SHARED_BASE,SHARED_BYTES,STATUS_OFFSET,256,LOG0_BASE,LOG1_BASE,LOG_SIZE,*SPM_BASES,SPM_BYTES,0x10050000,0x10051000,n,r,96,128,128,BUFFER_BYTES,WORK_BYTES,2*WORK_BYTES,2*r*n*128,0x240,0x248,56)
def load_plan(ep,mp):
    from elftools.elf.elffile import ELFFile
    ep=Path(ep).resolve();mp=Path(mp).resolve();raw=mp.read_bytes();m=json.loads(raw)
    if m['abi']!=ABI or m['cases']!=cases() or m['case_count']!=len(cases()) or not 1<=m['max_iterations']<=32:raise ValueError('Wrong test manifest/cases')
    if m['hardware']['spm_bases']!=list(SPM_BASES) or m['hardware']['spm_bytes']!=SPM_BYTES:raise ValueError('Wrong SPM map')
    if len(m['expected_hashes'])!=2 or any(len(h)!=m['max_iterations'] or any(len(r)!=m['case_count'] or any(type(x)!=int or not 0<=x<1<<64 for x in r) for r in h) for h in m['expected_hashes']):raise ValueError('Expected-hash table dimensions/types')
    with ep.open('rb') as stream:
        elf=ELFFile(stream)
        if elf.elfclass!=64 or not elf.little_endian or elf['e_machine']!='EM_RISCV' or elf['e_type']!='ET_EXEC' or elf['e_entry']!=APP_BASE or int(elf['e_flags'])&6!=4:raise ValueError('Expected RV64 lp64d ELF')
        segments=[]
        for s in elf.iter_segments():
            if s['p_type']!='PT_LOAD' or not s['p_memsz']:continue
            a,o,f,n,flags=map(int,(s['p_paddr'],s['p_offset'],s['p_filesz'],s['p_memsz'],s['p_flags']))
            if not APP_BASE<=a<a+n<=APP_END or not 0<=f<=n or a!=s['p_vaddr'] or o+f>ep.stat().st_size:raise ValueError('Invalid PT_LOAD')
            if any(a<x+y and x<a+n for x,_,_,y,_ in segments):raise ValueError('Overlapping segments')
            segments.append((a,o,f,n,flags))
        if not any(a<=APP_BASE<a+f and fl&1 for a,o,f,n,fl in segments):raise ValueError('Entry not executable')
        table=elf.get_section_by_name('.symtab')
        if table is None:raise ValueError('Keep ELF symbols')
        def sym(name):
            values=[s for s in table.get_symbol_by_name(name) or [] if s['st_shndx']!='SHN_UNDEF']
            if len(values)!=1:raise ValueError('Missing/ambiguous symbol '+name)
            return int(values[0]['st_value'])
        names='_start __bss_start __bss_end __image_end _stack0_bottom _stack0_top _stack1_bottom _stack1_top test_bundle_digest test_build_info test_cases test_records test_work'.split();sy={n:sym(n) for n in names}
        def read(a,n):
            for base,off,fs,ms,fl in segments:
                if base<=a and a+n<=base+fs:stream.seek(off+a-base);return stream.read(n)
            raise ValueError('Metadata not file backed')
        if read(sy['test_build_info'],256)!=struct.pack('<32Q',*build_info(m)) or read(sy['test_bundle_digest'],32)!=bytes.fromhex(sha(raw)):raise ValueError('ELF/manifest mismatch')
        for i,c in enumerate(m['cases']):
            if read(sy['test_cases']+i*96,96)!=struct.pack('<24I',*[c[k] for k in FIELDS]):raise ValueError('Case table differs')
        if tuple(sy[n] for n in ('_stack0_bottom','_stack0_top','_stack1_bottom','_stack1_top'))!=(STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP):raise ValueError('Stacks differ')
        if not APP_BASE<=sy['__bss_start']<=sy['__bss_end']==sy['__image_end']<=APP_END:raise ValueError('BSS bounds')
        ranges=[]
        for name,n in [('test_work',2*WORK_BYTES),('test_records',2*m['max_iterations']*m['case_count']*128)]:
            a=sy[name]
            if a%64 or not sy['__bss_start']<=a<a+n<=sy['__bss_end'] or any(a<x+y and x<a+n for x,y in ranges):raise ValueError('Workspace layout')
            ranges.append((a,n))
        if max(a+n for a,o,f,n,fl in segments)>sy['__image_end']:raise ValueError('Segment beyond image end')
    return dict(path=ep,manifest_path=mp,manifest=m,symbols=sy,segments=segments)
def verify_snapshot(raw,records,cfg,plan):
    m=plan['manifest'];sy=plan['symbols'];v=struct.unpack('<16Q',cfg);epoch,mask,suite,iters=v[2:6];n=m['case_count'];cap=m['max_iterations']
    if len(raw)!=SHARED_BYTES or raw[:128]!=cfg or len(records)!=2*cap*n*128:raise ValueError('Snapshot/config size')
    if struct.unpack_from('<Q',raw,0x80)[0]!=epoch or struct.unpack_from('<Q',raw,0x240)[0]!=1:raise ValueError('Boot/alias probe missing')
    for off in (0xc0,0x100,0x140,0x180,0x1c0,0x200):
        if struct.unpack_from('<Q',raw,off)[0]!=iters:raise ValueError('Barrier token mismatch')
    controls=struct.unpack_from('<28Q',raw,0x248)
    if any(controls):raise ValueError('ActiveSPM DMA controls/status not idle and untouched')
    statuses=[parse_status(raw[STATUS_OFFSET+256*h:STATUS_OFFSET+256*(h+1)]) for h in (0,1)]
    selected=[i for i,c in enumerate(m['cases']) if suite or c['kind']<=2];passed=[]
    for h,s in enumerate(statuses):
        count=len(selected)*iters if mask&(1<<h) else 0
        for k,e in [('state',DONE),('hart',h),('run_id',epoch),('mode',mask),('error',0),('mismatches',0),('tasks_done',count),('verified_tasks',count),('checked_stages',count),('iterations_done',iters),('warmup_done',0),('mcause',0),('mepc',0),('mtval',0)]:
            if s[k]!=e:raise ValueError('Hart '+str(h)+' bad '+k+': '+str(s[k]))
        for k,name in [('bss_start','__bss_start'),('bss_end','__bss_end'),('image_end','__image_end')]:
            if s[k]!=sy[name]:raise ValueError('Runtime BSS differs')
        for r in range(cap):
            for i,c in enumerate(m['cases']):
                offset=((h*cap+r)*n+i)*128;entry=parse_record(records[offset:offset+128]);active=bool(mask&(1<<h)) and r<iters and i in selected
                if not active:
                    if any(entry.values()):raise ValueError('Unexpected inactive record')
                    continue
                reference=m['expected_hashes'][h][r][i]
                wanted=dict(state=1,hart=h,iteration=r,case_index=i,output_address=SPM_BASES[h]+c['offset'],output_bytes=output_bytes(c),actual_hash=reference,reference_hash=reference,first_bad=(1<<64)-1,expected_byte=0,actual_byte=0,guard_ok=1,seed=seed(h,r,i),kind=c['kind'],reserved=0)
                if any(entry[k]!=e for k,e in wanted.items()) or entry['cycles']<=0:raise ValueError('Case record mismatch '+c['name']+': '+repr(entry))
                passed.append(dict(name=c['name'],**entry))
    return statuses,passed,controls
