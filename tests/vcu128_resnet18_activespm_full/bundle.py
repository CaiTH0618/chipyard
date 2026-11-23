"""Validate graph/ELF binding before any board access."""
from pathlib import Path
import json,struct
from memory_plan import FIELDS,sha,validate_bundle,build_info
from protocol import *

def load_plan(elf_path,manifest_path):
    from elftools.elf.elffile import ELFFile
    ep=Path(elf_path).resolve();mp=Path(manifest_path).resolve();raw=mp.read_bytes();m=json.loads(raw)
    model=m['model'];layout=m['layout'];blob=(mp.parent/'baseline_blob.bin').read_bytes();validate_bundle(m,blob)
    with ep.open('rb') as source:
        elf=ELFFile(source)
        if elf.elfclass!=64 or not elf.little_endian or elf['e_machine']!='EM_RISCV' or elf['e_type']!='ET_EXEC' or elf['e_entry']!=APP_BASE or int(elf['e_flags'])&6!=4:raise ValueError('Expected RV64 lp64d executable')
        segments=[]
        for seg in elf.iter_segments():
            if seg['p_type']!='PT_LOAD' or not seg['p_memsz']:continue
            a,o,f,n,flags=map(int,(seg['p_paddr'],seg['p_offset'],seg['p_filesz'],seg['p_memsz'],seg['p_flags']))
            if not APP_BASE<=a<a+n<=APP_END or not 0<=f<=n or a!=seg['p_vaddr'] or o+f>ep.stat().st_size:raise ValueError('Invalid PT_LOAD')
            if any(a<x+y and x<a+n for x,_,_,y,_ in segments):raise ValueError('Overlapping PT_LOAD')
            segments.append((a,o,f,n,flags))
        if not any(a<=APP_BASE<a+f and flags&1 for a,o,f,n,flags in segments):raise ValueError('Entry not executable')
        table=elf.get_section_by_name('.symtab')
        if table is None:raise ValueError('ELF must retain symbol table')
        def sym(name):
            ss=[s for s in table.get_symbol_by_name(name) or [] if s['st_shndx']!='SHN_UNDEF']
            if len(ss)!=1:raise ValueError('Missing/ambiguous symbol '+name)
            return int(ss[0]['st_value'])
        names='_start __bss_start __bss_end __image_end _stack0_bottom _stack0_top _stack1_bottom _stack1_top baseline_blob baseline_blob_end baseline_bundle_digest baseline_build_info spm_build_info baseline_work baseline_outputs baseline_results baseline_stages baseline_refs baseline_parents baseline_tasks'.split()
        sy={n:sym(n) for n in names}
        def read(a,n):
            for base,off,fs,ms,fl in segments:
                if base<=a and a+n<=base+fs:source.seek(off+a-base);return source.read(n)
            raise ValueError('Metadata not file backed')
        if read(sy['baseline_build_info'],256)!=struct.pack('<32Q',*build_info(m)):raise ValueError('Build ABI/layout differs')
        if read(sy['spm_build_info'],64)!=struct.pack('<8Q',*SPM_BASES,SPM_BYTES,0x10050000,0x10051000,80,0x600,0x670):raise ValueError('SPM mapping/layout differs')
        if read(sy['baseline_bundle_digest'],32)!=bytes.fromhex(sha(raw)):raise ValueError('ELF/manifest mismatch; rebuild')
        if sy['baseline_blob_end']-sy['baseline_blob']!=len(blob) or read(sy['baseline_blob'],len(blob))!=blob:raise ValueError('ELF blob differs')
        if (sy['_stack0_bottom'],sy['_stack0_top'],sy['_stack1_bottom'],sy['_stack1_top'])!=(STACK0_BOTTOM,STACK0_TOP,STACK1_BOTTOM,STACK1_TOP):raise ValueError('Stack layout differs')
        if not APP_BASE<=sy['__bss_start']<=sy['__bss_end']==sy['__image_end']<=APP_END:raise ValueError('BSS bounds differ')
        ranges=[]
        for name,n in [('baseline_work',layout['work_bytes']),('baseline_outputs',layout['output_bytes']),('baseline_results',m['task_count']*128)]:
            a=sy[name]
            if a%64 or not sy['__bss_start']<=a<a+n<=sy['__bss_end']:raise ValueError('Workspace outside aligned BSS')
            if any(a<x+y and x<a+n for x,y in ranges):raise ValueError('Workspaces overlap')
            ranges.append((a,n))
        for ti,sample in enumerate(model['samples']):
            expected=struct.pack('<4I',sample['task_id'],sample['first_stage'],30,ti*layout['final_frame_bytes']+64)
            if read(sy['baseline_tasks']+ti*16,16)!=expected:raise ValueError('Task table differs')
        for index,(c,p) in enumerate(zip(model['cases'],layout['stages'])):
            pointers=[]
            for key in ('a','b','d'):
                r=c[key];pointers.append(sy['baseline_blob']+r['offset'] if r and r['kind']=='blob' else 0)
            expected=struct.pack('<4Q21I4x',*pointers,sy['baseline_blob']+c['golden'],*[c.get(k,0) for k in FIELDS])
            if read(sy['baseline_stages']+index*120,120)!=expected:raise ValueError('Stage descriptor differs '+str(index))
            if read(sy['baseline_refs']+index*16,16)!=struct.pack('<4i',*p['refs'],p['out']):raise ValueError('Slot refs differ')
            if read(sy['baseline_parents']+index*12,12)!=struct.pack('<3i',*p['parents']):raise ValueError('Parent table differs')
        if max(a+n for a,o,f,n,fl in segments)>sy['__image_end']:raise ValueError('Segment past image end')
    return dict(path=ep,manifest_path=mp,manifest=m,model=model,layout=layout,blob=blob,symbols=sy,segments=segments)

def verify_snapshot(shared,records,cfg,plan):
    m=plan['manifest'];layout=plan['layout'];model=plan['model'];sy=plan['symbols']
    if len(shared)!=SHARED_BYTES or len(records)!=m['task_count']*128 or shared[:128]!=cfg:raise ValueError('Snapshot size/config changed')
    _,_,epoch,mode,swap,count,wait,check,warmup,repeats,storage,probe,*_=struct.unpack('<16Q',cfg)
    if any(struct.unpack_from('<28Q',shared,0x600)):raise ValueError('ActiveSPM DMA controls not idle/untouched')
    if struct.unpack_from('<Q',shared,0x80)[0]!=epoch:raise ValueError('Boot epoch mismatch')
    for off in [0xc0,0x100,0x140,0x180,0x1c0,0x200]:
        if struct.unpack_from('<Q',shared,off)[0]!=warmup+repeats:raise ValueError('Iteration barrier token mismatch')
    makespan=struct.unpack_from('<Q',shared,0x240)[0];times=list(struct.unpack_from('<32Q',shared,0x248)[:repeats])
    if any(x<=0 for x in times) or makespan!=sum(times):raise ValueError('Iteration timing missing/inconsistent')
    statuses=[parse_status(shared[STATUS_OFFSET+256*h:STATUS_OFFSET+256*(h+1)]) for h in [0,1]]
    for h,s in enumerate(statuses):
        tasks=sum(owner(i,mode,swap)==h for i in range(count));checked=tasks*30*(warmup+repeats) if check==0 else 0
        expected=(DONE,h,epoch,mode,0,tasks*repeats,tasks,0,checked,repeats,warmup)
        actual=tuple(s[k] for k in 'state hart run_id mode error tasks_done verified_tasks mismatches checked_stages iterations_done warmup_done'.split())
        if actual!=expected:raise ValueError('Hart counters/state differ: '+repr(s))
        base=SPM_BASES[h] if storage else sy['baseline_work']+h*layout['hart_work_bytes']
        if s['storage']!=storage or s['work_base']!=base or s['work_guard_ok']!=int(tasks>0):raise ValueError('Runtime workspace/guard policy differs')
        if any(s[k] for k in ['mcause','mepc','mtval']):raise ValueError('Unexpected trap')
        for field,symbol in [('bss_start','__bss_start'),('bss_end','__bss_end'),('image_end','__image_end')]:
            if s[field]!=sy[symbol]:raise ValueError('Runtime symbol mismatch')
    results=[]
    for ti,sample in enumerate(model['samples']):
        r=parse_task(records[ti*128:(ti+1)*128]);h=owner(ti,mode,swap)
        checked=30*(warmup+repeats) if check==0 else 0
        expected=(sample['task_id'],h,repeats,1,0,(1<<64)-1,(1<<64)-1,1,epoch,
                  sy['baseline_outputs']+ti*layout['final_frame_bytes']+64,4000,checked)
        actual=tuple(r[k] for k in 'task_id owner computed verified mismatches first_stage first_byte guard_ok run_id output_address output_bytes checked_stages'.split())
        if actual!=expected:raise ValueError('Task metadata mismatch: '+repr(r))
        last=model['cases'][sample['first_stage']+29];gold=plan['blob'][last['golden']:last['golden']+4000]
        if r['checksum']!=checksum(gold) or not 0<r['operator_cycles']<=r['cycles']:raise ValueError('Task checksum/timing mismatch')
        results.append(r)
    for h,s in enumerate(statuses):
        if s['operator_cycles']!=sum(r['operator_cycles'] for r in results if r['owner']==h):raise ValueError('Operator sum differs')
    return statuses,results,times
