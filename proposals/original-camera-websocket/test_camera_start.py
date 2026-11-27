import sys, io, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import setup as s
root=Path('/mock/app')
with patch.object(s,'require_idle'),patch.object(s,'app_running',side_effect=[False,False,True]),patch.object(s.subprocess,'run') as run,patch.object(s.LOCAL,'open',return_value=io.BytesIO(b'{"version":"1.0.0-rc2","state":"starting"}')),patch.object(s.time,'sleep'):
    s.start_camera(root); run.assert_called_once(); assert run.call_args.args[0]==['sh',str(root/'app.sh'),'start']
with patch.object(s,'require_idle'),patch.object(s,'app_running',return_value=True),patch.object(s.subprocess,'run') as run,patch.object(s.LOCAL,'open',return_value=io.BytesIO(b'{"version":"1.0.0-rc2","state":"ready"}')):
    s.start_camera(root); run.assert_not_called()
with patch.object(s,'require_idle',side_effect=RuntimeError('printing')),patch.object(s.subprocess,'run') as run:
    try: s.start_camera(root)
    except RuntimeError: pass
    else: raise AssertionError('printing allowed')
    run.assert_not_called()
with patch.object(s,'require_idle'),patch.object(s,'app_running',return_value=False),patch.object(s.subprocess,'run'),patch.object(s.time,'sleep'),patch.object(s.time,'monotonic',side_effect=[0,1,26]):
    try: s.start_camera(root)
    except RuntimeError: pass
    else: raise AssertionError('failed start accepted')
print('PASS: start and confirm worker, no duplicate start, print guard, missing worker detected')
