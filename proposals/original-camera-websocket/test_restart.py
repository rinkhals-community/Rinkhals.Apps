import sys, io, json, subprocess, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import setup as s
from contextlib import ExitStack

def response(camera):
    return io.BytesIO(json.dumps({'result':{'webcams':[camera]}}).encode())
valid={'name':'Webcam','enabled':True,'service':'hlsstream','stream_url':'/webcam/live.m3u8'}
old={**valid,'service':'mjpegstreamer'}
with tempfile.TemporaryDirectory() as d:
    root=Path(d); (root/'app.sh').touch()
    with patch.object(s,'root_for',return_value=root),patch.object(s,'require_idle') as idle,patch.object(s.subprocess,'run') as run,patch.object(s.LOCAL,'open',side_effect=[TimeoutError(),response(old),response(valid)]),patch.object(s.time,'sleep'):
        s.restart_moonraker(); idle.assert_called_once(); run.assert_called_once(); assert run.call_args.args[0]==['sh',str(root/'app.sh'),'start']
    with patch.object(s,'root_for',return_value=root),patch.object(s,'require_idle',side_effect=RuntimeError('printing')),patch.object(s.subprocess,'run') as run:
        try: s.restart_moonraker()
        except RuntimeError: pass
        else: raise AssertionError('printing allowed')
        run.assert_not_called()
    with patch.object(s,'root_for',return_value=root),patch.object(s,'require_idle'),patch.object(s.subprocess,'run',side_effect=subprocess.TimeoutExpired('start',30)),patch.object(s.LOCAL,'open') as op:
        try: s.restart_moonraker()
        except subprocess.TimeoutExpired: pass
        else: raise AssertionError('start timeout accepted')
        op.assert_not_called()
    with patch.object(s,'root_for',return_value=root),patch.object(s,'require_idle'),patch.object(s.subprocess,'run'),patch.object(s.LOCAL,'open',side_effect=TimeoutError),patch.object(s.time,'sleep'),patch.object(s.time,'monotonic',side_effect=[0,1,61]):
        try: s.restart_moonraker()
        except RuntimeError: pass
        else: raise AssertionError('missing webcam accepted')
print('PASS: restart once, wait for API and HLS config, block active print, handle service/API timeout')
