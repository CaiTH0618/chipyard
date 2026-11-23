#!/usr/bin/env python3
"""Fast synthetic dataflow/runtime/ELF checks. Not board or INT8 math validation."""
import copy,hashlib,json,os,shutil,struct,subprocess,tarfile,tempfile,unittest
from pathlib import Path
from memory_plan import make_layout,validate_bundle,sha
from prepare_baseline import prepare
from protocol import config
from bundle import load_plan,verify_snapshot
ROOT=Path(__file__).resolve().parent

def synthetic_export(directory):
    m=json.loads((ROOT/'test_fixture.json').read_text())
    blob=bytearray(m['blob_bytes'])
    for a in m['arrays']:
        if a['role']=='golden':
            if a['dtype'].endswith('i4'):
                data=b''.join(struct.pack('<i',i-500) for i in range(a['bytes']//4))
            else:
                value=hashlib.sha256(a['name'].encode()).digest()[0];data=bytes([value])*a['bytes']
        elif a['role']=='input':data=bytes([17])*a['bytes']
        else:data=bytes(a['bytes'])
        blob[a['offset']:a['offset']+a['bytes']]=data;a['sha256']=sha(data)
    m['blob_sha256']=sha(blob)
    directory.mkdir();(directory/'full_blob.bin').write_bytes(blob);(directory/'full_manifest.json').write_text(json.dumps(m))
    return m

class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.tmp=Path(cls.temp.name);cls.work=cls.tmp/'work';shutil.copytree(ROOT,cls.work,ignore=shutil.ignore_patterns('build','dist','generated','__pycache__'))
        cls.source=synthetic_export(cls.tmp/'source')
        cls.manifest=prepare(cls.tmp/'source',cls.work/'generated');cls.blob=(cls.work/'generated/baseline_blob.bin').read_bytes()
        subprocess.run(['gcc','-std=gnu99','-O2','-pthread','-Wall','-Wextra','-Werror','-DNATIVE_TEST','-I.','native_runner.c','runtime.c','generated/baseline_graph.c','generated/baseline_blob.S','-o','native_runner'],cwd=cls.work,check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def native(self,mode,check,swap=0,warm=0,repeats=1,injection=None):
        args=[str(self.work/'native_runner'),mode,check,str(swap),str(warm),str(repeats)]
        if injection:args.append(injection)
        env=dict(os.environ,BASELINE_SNAPSHOT_DIR=str(self.work))
        r=subprocess.run(args,cwd=self.work,env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        if not injection:
            shared=(self.work/'shared.bin').read_bytes();records=(self.work/'results.bin').read_bytes()
            from protocol import parse_task
            first=parse_task(records[:128]);sy={'baseline_outputs':first['output_address']-64,'__bss_start':0x82000000,'__bss_end':0x83000000,'__image_end':0x83000000}
            plan=dict(manifest=self.manifest,layout=self.manifest['layout'],model=self.manifest['model'],blob=self.blob,symbols=sy)
            cfg=config(0x123456789abcdef0,int(mode=='dual'),swap,4,200000000000,int(check=='perf'),warm,repeats)
            verify_snapshot(shared,records,cfg,plan)
        return r.stdout
    def test_plan_real_shapes_and_reuse(self):
        l=self.manifest['layout'];self.assertEqual(l['hart_work_bytes'],602496);self.assertEqual(l['peak_live_bytes'],[602112]*4)
        self.assertEqual(l['stages'][0]['out'],l['stages'][4]['out']) # a dead value's slot is reused
        self.assertLess(l['hart_work_bytes'],self.source['arena_bytes'])
        validate_bundle(self.manifest,self.blob)
    def test_reject_alias(self):
        m=copy.deepcopy(self.manifest);m['layout']['stages'][2]['out']=m['layout']['stages'][2]['refs'][0]
        with self.assertRaises(ValueError):validate_bundle(m,self.blob)
    def test_reject_wrong_predecessor(self):
        m=copy.deepcopy(self.source);m['cases'][2]['a']['offset']+=64
        with self.assertRaises(ValueError):make_layout(m)
    def test_serial_debug(self):self.native('serial','debug')
    def test_serial_perf_warmup_repeats(self):self.native('serial','perf',warm=1,repeats=3)
    def test_dual_debug(self):self.native('dual','debug',repeats=2)
    def test_dual_swap(self):self.native('dual','debug',swap=1,warm=1,repeats=2)
    def test_dual_perf(self):self.native('dual','perf',warm=2,repeats=3)
    def test_wrong_intermediate_debug(self):self.native('serial','debug',injection='data')
    def test_wrong_final_perf(self):self.native('dual','perf',injection='data')
    def test_guard_debug(self):self.native('serial','debug',injection='guard')
    def test_guard_perf(self):self.native('serial','perf',injection='guard')
    def test_reject_bad_host_options(self):
        for args in [(1,0,1,4,100,0,0,1),(1,0,0,4,100,0,0,0),(1,0,0,4,100,1,33,1)]:
            with self.assertRaises(ValueError):config(*args)
    def test_elf_binding_and_mutation(self):
        # A host-linked metadata fixture, with machine/flags changed ONLY to
        # exercise the ELF reader. It is NEVER executable on a RISC-V board.
        shim='''#include "platform.h"
        void rocket_main(uintptr_t);
        __attribute__((section(".text.init"))) void _start(void){rocket_main(0);for(;;){}}
        struct shared *platform_shared(void){return (void*)(uintptr_t)SHARED_BASE;}
        uint64_t platform_cycles(void){return 1;}
        uintptr_t platform_hart(void){return 0;}
        void platform_relax(void){}
        uint64_t platform_image_symbol(unsigned w){return 0;}
        void platform_stop(void){for(;;){}}
        void model_run(const struct qcase *c,void *p){}
        void *memset(void *p,int c,size_t n){unsigned char *q=p;while(n--)*q++=c;return p;}
        void *memcpy(void *p,const void *s,size_t n){unsigned char *q=p;const unsigned char *r=s;while(n--)*q++=*r++;return p;}
        '''
        (self.work/'shim.c').write_text(shim)
        (self.work/'native.ld').write_text((ROOT/'linker.ld').read_text().replace('OUTPUT_ARCH(riscv)','OUTPUT_ARCH(i386:x86-64)').replace('*(.bss .bss.*)','*(.bss .bss.* .lbss .lbss.*)'))
        objects=[]
        for index,name in enumerate(['shim.c','runtime.c','generated/baseline_graph.c','generated/baseline_blob.S']):
            out=f'obj{index}.o';objects.append(out)
            subprocess.run(['gcc','-c','-O2','-DNATIVE_TEST','-I.','-mcmodel=large','-fno-pic','-fno-pie','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-asynchronous-unwind-tables','-ffunction-sections','-fdata-sections',name,'-o',out],cwd=self.work,check=True,capture_output=True)
        subprocess.run(['ld','--gc-sections','-T','native.ld','-o','metadata.elf',*objects],cwd=self.work,check=True,capture_output=True)
        path=self.work/'metadata.elf';raw=bytearray(path.read_bytes());struct.pack_into('<H',raw,18,243);struct.pack_into('<I',raw,48,4);path.write_bytes(raw)
        plan=load_plan(path,self.work/'generated/baseline_manifest.json')
        self.assertEqual(plan['layout']['slot_count'],3)
        # The synthetic ELF may be inspected/packaged in this temporary directory,
        # but the Host must reject any attempt to run it on a board.
        self.assertTrue(plan['model']['test_fixture_only'])
        host=['python3','host_resnet18_cache_baseline.py','--elf','metadata.elf']
        dry=subprocess.run(host+['--dry-run'],cwd=self.work,capture_output=True,text=True)
        self.assertEqual(dry.returncode,0,dry.stderr)
        blocked=subprocess.run(host,cwd=self.work,capture_output=True,text=True)
        self.assertNotEqual(blocked.returncode,0)
        self.assertIn('Synthetic test fixture cannot run on a board',blocked.stderr)
        (self.work/'build').mkdir(exist_ok=True)
        shutil.copy2(path,self.work/'build/resnet18_cache_baseline.elf')
        packaged=subprocess.run(['python3','package_host.py'],cwd=self.work,capture_output=True,text=True)
        self.assertEqual(packaged.returncode,0,packaged.stderr)
        with tarfile.open(self.work/'dist/resnet18_cache_baseline_host.tar.gz') as archive:
            names=set(archive.getnames())
            self.assertTrue({'README.md','build/resnet18_cache_baseline.elf','generated/baseline_manifest.json','generated/baseline_blob.bin','host_resnet18_cache_baseline.py'}<=names)
        address=plan['symbols']['baseline_refs'];file_offset=None
        for base,off,fs,ms,fl in plan['segments']:
            if base<=address<base+fs:file_offset=off+address-base
        self.assertIsNotNone(file_offset)
        raw[file_offset]^=1;path.write_bytes(raw)
        with self.assertRaises(ValueError):load_plan(path,self.work/'generated/baseline_manifest.json')

if __name__=='__main__':unittest.main(verbosity=2)
