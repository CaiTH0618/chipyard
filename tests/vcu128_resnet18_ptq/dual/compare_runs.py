#!/usr/bin/env python3
"""Compare identical-workload board reports; never compare across binaries/data."""
import argparse
import json
from pathlib import Path

def compare_reports(serial,dual,swapped=None):
    if serial.get('status')!='PASS' or serial.get('mode')!='serial' or serial.get('swap'):raise ValueError('Need a successful normal serial report')
    if dual.get('status')!='PASS' or dual.get('mode')!='dual' or dual.get('swap'):raise ValueError('Need a successful normal dual report')
    identity=['elf_sha256','manifest_sha256','quant_manifest_sha256','source_full_manifest_sha256','sample_count','soc_mhz']
    def validate(other,expect_swap):
        if other.get('status')!='PASS' or other.get('mode')!='dual' or bool(other.get('swap'))!=expect_swap:raise ValueError('Wrong dual mode/swap report')
        for k in identity:
            if serial.get(k)!=other.get(k):raise ValueError('Different binary/data/clock: '+k)
        a={p['task_id']:p for p in serial['predictions']};b={p['task_id']:p for p in other['predictions']}
        if len(a)!=serial['sample_count'] or set(a)!=set(b):raise ValueError('Task identities differ')
        for key in a:
            for field in ['input_sha256','source_sha256','logits_int32']:
                if a[key][field]!=b[key][field]:raise ValueError('Task '+str(key)+' differs in '+field)
            if len(a[key]['logits_int32'])!=1000:raise ValueError('Missing complete logits')
        if serial['makespan_cycles']<=0 or other['makespan_cycles']<=0:raise ValueError('Missing makespan')
        return serial['makespan_cycles']/other['makespan_cycles']
    ratio=validate(dual,False)
    result=dict(sample_count=serial['sample_count'],all_logits_equal=True,serial_cycles=serial['makespan_cycles'],dual_cycles=dual['makespan_cycles'],speedup=ratio,dual_images_per_million_cycles=dual['images_per_million_cycles'],timing_note='Single batch; same ELF/data and same actual bitstream/clock required. No warm-up/repetition statistics.')
    if swapped is not None:result['swapped_speedup']=validate(swapped,True);result['swapped_cycles']=swapped['makespan_cycles']
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--serial',type=Path,required=True);p.add_argument('--dual',type=Path,required=True);p.add_argument('--swapped',type=Path);p.add_argument('--out',type=Path)
    a=p.parse_args();s=json.loads(a.serial.read_text());d=json.loads(a.dual.read_text());w=json.loads(a.swapped.read_text()) if a.swapped else None
    result=compare_reports(s,d,w)
    print('Same inputs and all 1000 INT32 logits per image match across runs')
    print(f"Serial: {result['serial_cycles']:,} cycles; dual: {result['dual_cycles']:,} cycles; speedup: {result['speedup']:.3f}x")
    if w:print(f"Swapped dual: {result['swapped_cycles']:,} cycles; speedup: {result['swapped_speedup']:.3f}x")
    if a.out:a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
