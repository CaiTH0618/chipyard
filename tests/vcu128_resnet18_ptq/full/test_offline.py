#!/usr/bin/env python3
"""Check ELF/graph binding, rejection paths and serialized Host-only packaging."""
import argparse
import copy
from host_resnet18_full import load_plan,verify_case,validate_graph
from protocol import checksum
p=argparse.ArgumentParser();p.add_argument('--elf',required=True);p.add_argument('--manifest',required=True)
a=p.parse_args();plan=load_plan(a.elf,a.manifest)
def rejects(fn):
    try:fn()
    except (ValueError,RuntimeError):return
    raise AssertionError('Invalid result was accepted')
for i,c in enumerate(plan['manifest']['cases']):
    expected=plan['blob'][c['golden']:c['golden']+c['bytes']]
    h=dict(hart=plan['hart'],stage=i+1,sequence=i+1,cases_done=i,
           output_address=plan['symbols']['full_output']+c['output_offset'],output_bytes=c['bytes'],
           mismatches=0,first_bad=0xffffffffffffffff,checksum=checksum(expected))
    prefix=bytes([0xc3])*64;suffix=bytes([0x3c])*64
    verify_case(plan,i,h,expected,prefix,suffix)
    wrong=bytearray(expected);wrong[len(wrong)//2]^=1
    rejects(lambda:verify_case(plan,i,h,bytes(wrong),prefix,suffix))
    rejects(lambda:verify_case(plan,i,h,expected,bytes(64),suffix))
    rejects(lambda:verify_case(plan,i,{**h,'hart':1-plan['hart']},expected,prefix,suffix))
    rejects(lambda:verify_case(plan,i,{**h,'output_address':h['output_address']+64},expected,prefix,suffix))
m=copy.deepcopy(plan['manifest']);m['cases'][1]['a']=dict(kind='blob',offset=m['cases'][0]['golden'],node='stem.pool')
rejects(lambda:validate_graph(m,plan['blob']))
m=copy.deepcopy(plan['manifest']);m['cases'][2]['a']=dict(m['cases'][1]['a'])
rejects(lambda:validate_graph(m,plan['blob']))
m=copy.deepcopy(plan['manifest']);m['buffers'][1]['offset']=m['buffers'][0]['offset']
rejects(lambda:validate_graph(m,plan['blob']))
print(f"OFFLINE FULL CHECK PASS: hart{plan['hart']}, {plan['manifest']['count']} stages; output/guard/hart/address/golden-injection/edge/overlap errors rejected")
