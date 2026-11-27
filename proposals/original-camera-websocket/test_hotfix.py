import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import setup as s
assert s.detect_model({'modelId':'20025'})=='20025'
assert s.detect_model({'printer':{'modelId':20025}})=='20025'
assert s.detect_model({'items':[{'modelId':'20025'}]})=='20025'
assert s.detect_model({'modelId':'20025','nested':{'modelId':20025}})=='20025'
for obj in ({}, {'modelId':None}, {'modelId':True}, {'modelId':'20025','nested':{'modelId':20029}}):
    try: s.detect_model(obj)
    except RuntimeError: pass
    else: raise AssertionError('Invalid or ambiguous model accepted')
assert s.detect_model({'modelId':'20029'})!='20025'
print('PASS: nested model ID, duplicate same ID, malformed/missing/conflicting IDs, other model.')
