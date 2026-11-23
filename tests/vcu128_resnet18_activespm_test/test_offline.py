import json,os,shutil,struct,subprocess,tempfile,unittest
from pathlib import Path
from bundle import verify_snapshot
from protocol import config
from prepare_test import prepare
from test_plan import expected,seed,BUFFER_BYTES,WORK_BYTES
ROOT=Path(__file__).resolve().parent
class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.work=Path(cls.temp.name)/'work';shutil.copytree(ROOT,cls.work,ignore=shutil.ignore_patterns('build','generated','dist','reports','__pycache__'))
        # Use the same MVIN/ACC scale definitions frozen by the real compiler.
        candidates=[Path(os.environ.get('GEMMINI_SW','/nonexistent'))/'include/gemmini_params.h',ROOT/'build/inputs/include/gemmini_params.h']
        params=next((p for p in candidates if p.is_file()),None)
        if params is None:raise RuntimeError('Run make headers first, or set GEMMINI_SW to the matching Gemmini software tree')
        (cls.work/'include').mkdir();shutil.copy2(params,cls.work/'include/gemmini_params.h')
        cls.m=prepare(cls.work/'generated',4)
        subprocess.run(['gcc','-O2','-std=gnu99','-pthread','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-DNATIVE_TEST','-I.','native_runner.c','runtime.c','reference.c','generated/test_cases.c','-o','native_runner'],cwd=cls.work,check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_case(self,mask,suite,iterations,injection='none'):
        run=subprocess.run([str(self.work/'native_runner'),str(mask),str(suite),str(iterations),injection,str(self.work)],capture_output=True,text=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stderr+run.stdout)
        if injection!='none':return
        cfg=config(0x123456789abcdef0,mask,suite,iterations,20000000000,4)
        plan=dict(manifest=self.m,symbols={'__bss_start':0x82000000,'__bss_end':0x83000000,'__image_end':0x83000000})
        raw=(self.work/'shared.bin').read_bytes();records=(self.work/'records.bin').read_bytes();statuses,passed,_=verify_snapshot(raw,records,cfg,plan)
        selected=sum(suite or c['kind']<=2 for c in self.m['cases'])
        self.assertEqual(len(passed),mask.bit_count()*iterations*selected)
        if suite:
            work=(self.work/'work.bin').read_bytes();i=self.m['case_count']-1
            for h in (0,1):
                if mask&(1<<h):
                    golden=expected(self.m['cases'][i],seed(h,iterations-1,i));at=h*WORK_BYTES+BUFFER_BYTES+64
                    self.assertEqual(work[at:at+len(golden)],golden)
        # Metadata corruption must fail even if the target said DONE.
        bad=bytearray(records);first=next(x for x in passed);offset=((first['hart']*4+first['iteration'])*self.m['case_count']+first['case_index'])*128+48;bad[offset]^=1
        with self.assertRaises(ValueError):verify_snapshot(raw,bad,cfg,plan)
    def test_hart0_cpu(self):self.run_case(1,0,1)
    def test_hart1_cpu(self):self.run_case(2,0,2)
    def test_hart0_all(self):self.run_case(1,1,4)
    def test_hart1_all(self):self.run_case(2,1,4)
    def test_dual_all(self):self.run_case(3,1,4)
    def test_alias_detected(self):self.run_case(1,0,1,'alias')
    def test_control_detected(self):self.run_case(1,0,1,'control')
    def test_data_detected(self):self.run_case(1,1,1,'data')
    def test_guard_detected(self):self.run_case(1,1,1,'guard')
    def test_copy_detected(self):self.run_case(1,1,1,'copy')
    def test_config_rejects(self):
        for mask,suite,iters in [(0,0,1),(4,0,1),(1,2,1),(1,1,5),(1,1,0)]:
            with self.assertRaises(ValueError):config(1,mask,suite,iters,100,4)
if __name__=='__main__':unittest.main(verbosity=2)
