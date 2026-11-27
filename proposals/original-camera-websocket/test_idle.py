import sys, io, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import setup as s

def status(a,b): return {'print_stats':{'state':a},'idle_timeout':{'state':b}}
for a in ('standby','complete','cancelled'):
    for b in ('Idle','Ready',a,a.upper()):
        assert s.idle_status(status(a,b))[0],(a,b)
for a in ('printing','paused','onpause','heating','leveling','resonance','error','failed','',None,1,'unexpected'):
    for b in ('Idle','Ready',a,'standby'):
        assert not s.idle_status(status(a,b))[0],(a,b)
for b in ('printing','paused','heating','error','complete','',None,1):
    assert not s.idle_status(status('standby',b))[0]
for obj in ({},None,[],{'print_stats':{'state':'standby'}},{'print_stats':None,'idle_timeout':{}}):
    assert not s.idle_status(obj)[0]
for a,b,expected in [('standby','standby',True),('printing','printing',False),('paused','Ready',False)]:
    with patch.object(s.LOCAL,'open',return_value=io.BytesIO(json.dumps({'result':{'status':status(a,b)}}).encode())):
        assert s.printer_idle()==expected
with patch.object(s.LOCAL,'open',side_effect=TimeoutError): assert not s.printer_idle()
with patch.object(s.LOCAL,'open',return_value=io.BytesIO(b'invalid')): assert not s.printer_idle()
print('PASS: mirrored GoKlipper states, normal Klipper states, active and paused states, conflicting/missing states, timeout and invalid JSON')
