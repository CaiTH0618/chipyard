#!/usr/bin/env python3
"""No-device checks: ELF binding and Host rejection of incorrect outputs/guards/hart."""
import argparse
from host_resnet18_qops import load_plan,verify_case
from protocol import checksum
p=argparse.ArgumentParser();p.add_argument('--elf',required=True);p.add_argument('--manifest',required=True)
a=p.parse_args();plan=load_plan(a.elf,a.manifest)
def rejects(fn):
 try:fn()
 except (ValueError,RuntimeError):return
 raise AssertionError('incorrect result was accepted')
for index,c in enumerate(plan['manifest']['cases']):
 data=plan['blob'][c['golden']:c['golden']+c['bytes']]
 h={'hart':plan['hart'],'stage':index+1,'sequence':index+1,'cases_done':index,
    'output_address':plan['symbols']['qops_output']+64,'output_bytes':c['bytes'],
    'mismatches':0,'first_bad':0xffffffffffffffff,'checksum':checksum(data)}
 prefix=bytes([0xc3])*64;suffix=bytes([0x3c])*64
 verify_case(plan,index,h,data,prefix,suffix)
 changed=bytearray(data);changed[len(data)//2]^=1
 rejects(lambda:verify_case(plan,index,h,bytes(changed),prefix,suffix))
 rejects(lambda:verify_case(plan,index,h,data,bytes(64),suffix))
 rejects(lambda:verify_case(plan,index,{**h,'hart':1-plan['hart']},data,prefix,suffix))
print(f"OFFLINE CHECK PASS: hart{plan['hart']}; {plan['manifest']['count']} cases; mutated output, guard and wrong-hart results rejected")
