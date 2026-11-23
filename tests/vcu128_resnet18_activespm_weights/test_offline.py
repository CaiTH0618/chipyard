#!/usr/bin/env python3
"""Fast synthetic dataflow/runtime/ELF checks. Not board or INT8 math validation."""
import copy,hashlib,json,os,shutil,struct,subprocess,tarfile,tempfile,unittest
from pathlib import Path
from memory_plan import make_layout,validate_bundle,sha
from prepare_weights import prepare
from protocol import config
from bundle import load_plan,verify_snapshot
from compare_performance import compare as compare_reports
from weight_plan import make_weight_plan
from weight_report import measurements
from compare_weights import compare as compare_weight_reports
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
        else:data=(hashlib.sha256(a['name'].encode()).digest()*((a['bytes']+31)//32))[:a['bytes']]
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
        subprocess.run(['gcc','-std=gnu99','-O2','-pthread','-Wall','-Wextra','-Werror','-DNATIVE_TEST','-I.','native_runner.c','runtime.c','resident_weights.c','generated/baseline_graph.c','generated/baseline_blob.S','-o','native_runner'],cwd=cls.work,check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def native(self,mode,check,swap=0,warm=0,repeats=1,injection=None,storage=0,probe=0,weights=0):
        args=[str(self.work/'native_runner'),mode,check,str(swap),str(warm),str(repeats)]
        args.extend([injection or 'none',str(storage),str(probe),str(weights)])
        env=dict(os.environ,BASELINE_SNAPSHOT_DIR=str(self.work))
        r=subprocess.run(args,cwd=self.work,env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        if not injection:
            shared=(self.work/'shared.bin').read_bytes();records=(self.work/'results.bin').read_bytes()
            from protocol import parse_task
            first=parse_task(records[:128]);sy={'baseline_outputs':first['output_address']-64,'__bss_start':0x82000000,'__bss_end':0x83000000,'__image_end':0x83000000}
            sy.update(json.loads((self.work/'symbols.json').read_text()))
            plan=dict(manifest=self.manifest,layout=self.manifest['layout'],model=self.manifest['model'],blob=self.blob,symbols=sy)
            cfg=config(0x123456789abcdef0,{'serial':0,'dual':1,'hart1':2}[mode],swap,4,200000000000,int(check=='perf'),warm,repeats,storage,probe,weights)
            verify_snapshot(shared,records,cfg,plan)
            extra=measurements(shared,(self.work/'stages.bin').read_bytes(),self.manifest,bool(weights),repeats,warm)
            self.assertEqual(len(extra['per_node_timings']),30)
            self.assertEqual(extra['setup_plus_first_batch_cycles'] is None,warm>0)
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
    def test_spm_serial_debug(self):self.native('serial','debug',warm=1,repeats=5,storage=1,probe=1)
    def test_spm_serial_perf(self):self.native('serial','perf',warm=1,repeats=5,storage=1,probe=1)
    def test_spm_hart1_debug(self):self.native('hart1','debug',warm=1,repeats=2,storage=1,probe=1)
    def test_spm_hart1_perf(self):self.native('hart1','perf',warm=1,repeats=2,storage=1,probe=1)
    def test_spm_dual_debug(self):self.native('dual','debug',warm=1,repeats=2,storage=1,probe=1)
    def test_spm_dual_swap_perf(self):self.native('dual','perf',swap=1,warm=1,repeats=3,storage=1,probe=1)
    def test_b1_hbm_activespm(self):self.native('serial','perf',warm=1,repeats=5,storage=0,probe=1)
    def test_resident_serial_debug(self):self.native('serial','debug',warm=1,repeats=5,storage=1,probe=1,weights=1)
    def test_resident_serial_perf(self):self.native('serial','perf',warm=1,repeats=5,storage=1,probe=1,weights=1)
    def test_resident_first_batch(self):self.native('serial','perf',warm=0,repeats=1,storage=1,probe=1,weights=1)
    def test_resident_hart1(self):self.native('hart1','debug',warm=1,repeats=2,storage=1,probe=1,weights=1)
    def test_resident_dual(self):self.native('dual','debug',warm=1,repeats=2,storage=1,probe=1,weights=1)
    def test_resident_dual_swap(self):self.native('dual','perf',swap=1,warm=1,repeats=2,storage=1,probe=1,weights=1)
    def test_resident_copy_corruption(self):self.native('serial','debug',storage=1,probe=1,weights=1,injection='weight-copy')
    def test_resident_final_corruption(self):self.native('serial','perf',storage=1,probe=1,weights=1,injection='weight-final')
    def test_resident_guard_corruption(self):self.native('serial','debug',storage=1,probe=1,weights=1,injection='weight-guard')
    def test_weight_plan_capacity_and_shared_arrays(self):
        p=self.manifest['weights'];self.assertEqual(p['payload_bytes'],419008);self.assertEqual(len(p['entries']),9)
        self.assertEqual(p['occupied_bytes'],1022656)
        self.assertLessEqual(p['occupied_bytes'],1048576)
        self.assertEqual(p['stage_refs'][:30],p['stage_refs'][30:60])
        q=make_weight_plan(self.source,self.manifest['layout'],'layer2-large')
        self.assertEqual(q['payload_bytes'],442368);self.assertEqual(len(q['entries']),3)
        with self.assertRaises(ValueError):make_weight_plan(self.source,self.manifest['layout'],'manual',['logits'])
        with self.assertRaises(ValueError):make_weight_plan(self.source,self.manifest['layout'],'manual',['missing'])
        with self.assertRaises(ValueError):config(1,0,0,4,100,1,1,5,0,1,1)
    def test_reject_weight_plan_alias(self):
        m=copy.deepcopy(self.manifest);m['weights']['entries'][0]['data_offset']=64
        with self.assertRaises(ValueError):validate_bundle(m,self.blob)
    def test_alternative_profiles_execute(self):
        for profile,nodes in [('layer2-large',None),('manual',['layer3.0.conv1_relu','layer4.0.skip_projection'])]:
            work=self.tmp/profile
            shutil.copytree(ROOT,work,ignore=shutil.ignore_patterns('build','dist','generated','__pycache__'))
            prepare(self.tmp/'source',work/'generated',profile,nodes)
            subprocess.run(['gcc','-std=gnu99','-O2','-pthread','-Wall','-Wextra','-Werror','-DNATIVE_TEST','-I.','native_runner.c','runtime.c','resident_weights.c','generated/baseline_graph.c','generated/baseline_blob.S','-o','native_runner'],cwd=work,check=True,capture_output=True)
            r=subprocess.run([str(work/'native_runner'),'serial','debug','0','1','2','none','1','1','1'],cwd=work,capture_output=True,text=True,timeout=30)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_weight_comparison_with_runtime_snapshots(self):
        def report(weights):
            self.native('serial','perf',warm=1,repeats=2,storage=1,probe=1,weights=weights)
            from protocol import parse_status,parse_task
            raw=(self.work/'shared.bin').read_bytes()
            records=(self.work/'results.bin').read_bytes()
            r=dict(status='PASS',abi=4,check='perf',hardware='activespm',storage='spm',soc_mhz=50,
                   eligible_for_performance_comparison=True,activespm_dma_started=False,
                   work_guards_verified_by_target=True,final_outputs_checked=4,sample_count=4,
                   repeats=2,warmup=1,measured_images=8,mode='serial',swap=False,poll_sec=1.0,
                   active_harts=[0],final_check_scope='last measured iteration',cache_condition='warmup1',
                   capacity=dict(participating_storage_bytes=2097152),bitstream_sha256=None,
                   iteration_cycles=list(struct.unpack_from('<2Q',raw,0x248)),makespan_cycles=struct.unpack_from('<Q',raw,0x240)[0],
                   statuses=[parse_status(raw[0x400+h*256:0x500+h*256]) for h in (0,1)],
                   task_results=[parse_task(records[i*128:(i+1)*128]) for i in range(4)],
                   predictions=[dict(source_sha256=str(i)*64,logits_int32=[i]*1000) for i in range(4)])
            for k in ['elf_sha256','manifest_sha256','backend_sha256','resadd_direct_sha256','runtime_sha256','layout_sha256','model_blob_sha256','source_full_manifest_sha256','quant_manifest_sha256']:r[k]='a'*64
            r.update(measurements(raw,(self.work/'stages.bin').read_bytes(),self.manifest,bool(weights),2,1))
            return r
        b,r=report(0),report(1)
        result=compare_weight_reports(b,r)
        self.assertEqual(len(result['per_node']),30)
        self.assertEqual(len(result['selected_weights']),9)
        for key,value in [('elf_sha256','b'*64),('weight_mode','none'),('resident_weights_verified_before_and_after',False),('check','debug')]:
            bad=copy.deepcopy(r);bad[key]=value
            with self.assertRaises(ValueError):compare_weight_reports(b,bad)
        bad=copy.deepcopy(r);bad['predictions'][0]['logits_int32'][999]+=1
        with self.assertRaises(ValueError):compare_weight_reports(b,bad)
    def test_spm_corrupt_output(self):self.native('serial','perf',injection='data',storage=1,probe=1)
    def test_spm_guard(self):self.native('serial','debug',injection='guard',storage=1,probe=1)
    def test_spm_control(self):self.native('serial','perf',injection='control',storage=1,probe=1)
    def test_reject_storage_hardware_config(self):
        for storage,probe in [(1,0),(2,1),(0,2)]:
            with self.assertRaises(ValueError):config(1,0,0,4,100,1,1,5,storage,probe)
    def test_wrong_intermediate_debug(self):self.native('serial','debug',injection='data')
    def test_wrong_final_perf(self):self.native('dual','perf',injection='data')
    def test_guard_debug(self):self.native('serial','debug',injection='guard')
    def test_guard_perf(self):self.native('serial','perf',injection='guard')
    def test_reject_bad_host_options(self):
        for args in [(1,0,1,4,100,0,0,1),(1,0,0,4,100,0,0,0),(1,0,0,4,100,1,33,1)]:
            with self.assertRaises(ValueError):config(*args)
    def test_comparison_contract(self):
        b0=dict(status='PASS',check='perf',eligible_for_performance_comparison=True,abi=4,weight_mode='none',soc_mhz=50,work_guards_verified_by_target=True,activespm_dma_started=False,statuses=[dict(state=3,error=0,mismatches=0)]*2,final_outputs_checked=4,sample_count=4,
          hardware='cacheonly',storage='hbm',capacity=dict(participating_storage_bytes=2097152),iteration_cycles=[200]*5,makespan_cycles=1000,measured_images=20,repeats=5,mode='serial',swap=False,warmup=1,poll_sec=1.0,active_harts=[0],final_check_scope='last measured iteration',cache_condition='warmup1',bitstream_sha256=None,
          predictions=[dict(source_sha256=str(i)*64,logits_int32=[i]*1000) for i in range(4)],task_results=[dict(owner=0)]*4)
        for k in ['elf_sha256','manifest_sha256','backend_sha256','resadd_direct_sha256','runtime_sha256','layout_sha256','model_blob_sha256','source_full_manifest_sha256','quant_manifest_sha256']:b0[k]='a'*64
        b2=copy.deepcopy(b0);b2.update(hardware='activespm',storage='spm',iteration_cycles=[100]*5,makespan_cycles=500)
        b1=copy.deepcopy(b0);b1.update(hardware='activespm');b1['capacity']['participating_storage_bytes']=1048576
        self.assertEqual(compare_reports(b0,b2,b1)['B2_speedup_over_B0'],2)
        b3=copy.deepcopy(b2);b3.update(weight_mode='resident',resident_weights_verified_before_and_after=True)
        self.assertEqual(compare_reports(b0,b3)['B3_speedup_over_B0'],2)
        for field,value in [('elf_sha256','b'*64),('check','debug'),('poll_sec',.1),('abi',2)]:
            bad=copy.deepcopy(b2);bad[field]=value
            with self.assertRaises(ValueError):compare_reports(b0,bad)
        bad=copy.deepcopy(b2);bad['capacity']['participating_storage_bytes']=3145728
        with self.assertRaises(ValueError):compare_reports(b0,bad)
        bad=copy.deepcopy(b2);bad['predictions'][0]['logits_int32'][999]+=1
        with self.assertRaises(ValueError):compare_reports(b0,bad)
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
        uintptr_t native_spm_base(unsigned h){return h?SPM1_BASE:SPM0_BASE;}
        uint64_t native_spm_control(unsigned h,unsigned r){return 0;}
        void native_weight_hook(unsigned p,unsigned h){}
        uint64_t platform_image_symbol(unsigned w){return 0;}
        void platform_stop(void){for(;;){}}
        void model_run(const struct qcase *c,void *p){}
        void *memset(void *p,int c,size_t n){unsigned char *q=p;while(n--)*q++=c;return p;}
        void *memcpy(void *p,const void *s,size_t n){unsigned char *q=p;const unsigned char *r=s;while(n--)*q++=*r++;return p;}
        '''
        (self.work/'shim.c').write_text(shim)
        (self.work/'native.ld').write_text((ROOT/'linker.ld').read_text().replace('OUTPUT_ARCH(riscv)','OUTPUT_ARCH(i386:x86-64)').replace('*(.bss .bss.*)','*(.bss .bss.* .lbss .lbss.*)'))
        objects=[]
        for index,name in enumerate(['shim.c','runtime.c','resident_weights.c','generated/baseline_graph.c','generated/baseline_blob.S']):
            out=f'obj{index}.o';objects.append(out)
            subprocess.run(['gcc','-c','-O2','-DNATIVE_TEST','-I.','-mcmodel=large','-fno-pic','-fno-pie','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-asynchronous-unwind-tables','-ffunction-sections','-fdata-sections',name,'-o',out],cwd=self.work,check=True,capture_output=True)
        subprocess.run(['ld','--gc-sections','-T','native.ld','-o','metadata.elf',*objects],cwd=self.work,check=True,capture_output=True)
        path=self.work/'metadata.elf';raw=bytearray(path.read_bytes());struct.pack_into('<H',raw,18,243);struct.pack_into('<I',raw,48,4);path.write_bytes(raw)
        plan=load_plan(path,self.work/'generated/baseline_manifest.json')
        self.assertEqual(plan['layout']['slot_count'],3)
        # The synthetic ELF may be inspected/packaged in this temporary directory,
        # but the Host must reject any attempt to run it on a board.
        self.assertTrue(plan['model']['test_fixture_only'])
        host=['python3','host_resnet18_activespm_weights.py','--elf','metadata.elf']
        dry=subprocess.run(host+['--dry-run'],cwd=self.work,capture_output=True,text=True)
        self.assertEqual(dry.returncode,0,dry.stderr)
        blocked=subprocess.run(host,cwd=self.work,capture_output=True,text=True)
        self.assertNotEqual(blocked.returncode,0)
        self.assertIn('Synthetic test fixture cannot run on a board',blocked.stderr)
        (self.work/'build').mkdir(exist_ok=True)
        shutil.copy2(path,self.work/'build/resnet18_activespm_weights.elf')
        defaults=subprocess.run(['python3','host_resnet18_activespm_weights.py','--weights','resident','--dry-run'],cwd=self.work,capture_output=True,text=True)
        self.assertEqual(defaults.returncode,0,defaults.stderr)
        packaged=subprocess.run(['python3','package_host.py'],cwd=self.work,capture_output=True,text=True)
        self.assertEqual(packaged.returncode,0,packaged.stderr)
        with tarfile.open(self.work/'dist/resnet18_activespm_weights_host.tar.gz') as archive:
            names=set(archive.getnames())
            self.assertTrue({'README.md','build/resnet18_activespm_weights.elf','generated/baseline_manifest.json','generated/baseline_blob.bin','host_resnet18_activespm_weights.py'}<=names)
        for symbol in ('baseline_refs','baseline_weight_refs','baseline_resident_weights','weight_build_info'):
            address=plan['symbols'][symbol];file_offset=None
            for base,off,fs,ms,fl in plan['segments']:
                if base<=address<base+fs:file_offset=off+address-base
            self.assertIsNotNone(file_offset)
            changed=bytearray(raw);changed[file_offset]^=1;path.write_bytes(changed)
            with self.assertRaises(ValueError):load_plan(path,self.work/'generated/baseline_manifest.json')

if __name__=='__main__':unittest.main(verbosity=2)
