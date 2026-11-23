#!/usr/bin/env python3
"""Compare matching full-model B0/B2 reports, optionally with the B1 ablation."""
import argparse,json,statistics
from pathlib import Path

def metrics(r):
    t=r['iteration_cycles'];hz=r['soc_mhz']*1e6
    if len(t)!=r['repeats'] or any(type(x)!=int or x<=0 for x in t) or sum(t)!=r['makespan_cycles']:raise ValueError('Invalid timing series')
    if r['measured_images']!=r['sample_count']*r['repeats']:raise ValueError('Invalid measured image count')
    return dict(mean_batch_cycles=statistics.mean(t),median_batch_cycles=statistics.median(t),
        mean_batch_seconds=statistics.mean(t)/hz,mean_seconds_per_image=sum(t)/hz/r['measured_images'],
        images_per_second=r['measured_images']*hz/sum(t),
        min_max_spread_percent=(max(t)-min(t))/statistics.mean(t)*100,
        iteration_cycles=t)

def compare(b0,b2,b1=None):
    reports=[b0,b2]+([b1] if b1 else [])
    for r in reports:
        if r.get('status')!='PASS' or r.get('check')!='perf' or not r.get('eligible_for_performance_comparison') or not r.get('soc_mhz',0):raise ValueError('Need passing PERF reports with actual SoC frequency')
        if r.get('abi')!=3:raise ValueError('Use this full-model program for B0/B1/B2; older baseline reports use a different runtime. Preserve old reports as historical results and rerun B0 with this ELF.')
        if not r.get('work_guards_verified_by_target') or r.get('activespm_dma_started') is not False:raise ValueError('Missing guards or unexpected ActiveSPM DMA')
        if any(s['state']!=3 or s['error'] or s['mismatches'] for s in r['statuses']):raise ValueError('Target state not clean')
        if r['final_outputs_checked']!=r['sample_count']:raise ValueError('Final logits check incomplete')
        metrics(r)
    keys=['elf_sha256','manifest_sha256','backend_sha256','resadd_direct_sha256','runtime_sha256','layout_sha256','model_blob_sha256','source_full_manifest_sha256','quant_manifest_sha256','mode','swap','warmup','repeats','sample_count','soc_mhz','poll_sec','active_harts','final_check_scope','cache_condition']
    if any(k not in r for r in reports for k in keys):raise ValueError('Missing comparison metadata')
    if any(not isinstance(r[k],str) or len(r[k])!=64 for r in reports for k in keys[:9]):raise ValueError('Missing hash identity')
    for r in reports[1:]:
        for k in keys:
            if r.get(k)!=b0.get(k):raise ValueError('Reports differ in '+k)
        if [x['source_sha256'] for x in r['predictions']]!=[x['source_sha256'] for x in b0['predictions']]:raise ValueError('Inputs/order differ')
        if [x['logits_int32'] for x in r['predictions']]!=[x['logits_int32'] for x in b0['predictions']]:raise ValueError('Full logits differ')
        if [x['owner'] for x in r['task_results']]!=[x['owner'] for x in b0['task_results']]:raise ValueError('Task assignment differs')
    if (b0['hardware'],b0['storage'])!=('cacheonly','hbm') or (b2['hardware'],b2['storage'])!=('activespm','spm'):raise ValueError('Need B0=cacheonly/HBM and B2=ActiveSPM/SPM')
    if b0['capacity']['participating_storage_bytes']!=b2['capacity']['participating_storage_bytes']:raise ValueError('Participating capacities differ; current dual load has 2 MiB vs 3 MiB and is not an equal-capacity comparison')
    m0,m2=metrics(b0),metrics(b2);result=dict(status='PASS',comparison='B0 vs B2 equal participating capacity',participating_bytes=b0['capacity']['participating_storage_bytes'],B0=m0,B2=m2,
        B2_speedup_over_B0=m0['mean_batch_seconds']/m2['mean_batch_seconds'],B2_latency_reduction_percent=(1-m2['mean_batch_seconds']/m0['mean_batch_seconds'])*100,
        caveat='Hardware selection/capacity labels are operator declarations, not automatic bitstream identification. These are latency/throughput measurements; no HBM-traffic counter was collected.')
    if b1:
        if (b1['hardware'],b1['storage'])!=('activespm','hbm'):raise ValueError('B1 must be ActiveSPM hardware/HBM storage')
        if b1['bitstream_sha256'] and b2['bitstream_sha256'] and b1['bitstream_sha256']!=b2['bitstream_sha256']:raise ValueError('B1/B2 bitstream hashes differ')
        m1=metrics(b1);result.update(B1=m1,B1_slowdown_vs_B0=m1['mean_batch_seconds']/m0['mean_batch_seconds'],B2_speedup_over_B1=m1['mean_batch_seconds']/m2['mean_batch_seconds'])
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--b0',type=Path,required=True);p.add_argument('--b2',type=Path,required=True);p.add_argument('--b1',type=Path);p.add_argument('--out',type=Path,default=Path('reports/performance_comparison.json'));a=p.parse_args()
    result=compare(json.loads(a.b0.read_text()),json.loads(a.b2.read_text()),json.loads(a.b1.read_text()) if a.b1 else None)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2)+'\n')
    for label in ('B0','B1','B2'):
        if label in result:print(label+':',json.dumps(result[label]))
    print('B2 speedup over B0:',result['B2_speedup_over_B0']);print('Saved:',a.out)
