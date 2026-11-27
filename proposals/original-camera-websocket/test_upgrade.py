import sys,tempfile,json,shutil
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import setup as s, upgrade as u
stage=Path(__file__).parent/'39-original-camera-hls'
for scenario in ('ok','from024','from026','from027','from028','from029','fromrc1','disabled','failure','printing','already'):
    with tempfile.TemporaryDirectory() as d:
        home=Path(d); apps=home/'apps'; dest=apps/s.APP; dest.mkdir(parents=True)
        version=u.VERSION if scenario=='already' else ({'from024':'0.2.4-beta','from026':'0.2.6-beta','from027':'0.2.7-beta','from028':'0.2.8-beta','from029':'0.2.9-beta','fromrc1':'1.0.0-rc1'}.get(scenario,'0.2.2-beta'))
        (dest/'app.json').write_text(json.dumps({'name':'Anycubic Original Camera - WebSocket Bridge' if scenario=='already' else 'Anycubic Original Camera - HLS Bridge','version':version}))
        (dest/'app.sh').write_text('old'); (dest/'old.py').write_text('old file'); (dest/('.disabled' if scenario=='disabled' else '.enabled')).touch()
        conf=home/'moonraker.custom.conf'; conf.write_text(s.CAMERA); cfg=apps/(s.APP+'.config'); state=home/'state.json'; old=b'[webcam Webcam]\nservice: mjpegstreamer\n'; state.write_text(json.dumps({'phase':'ready','files':{str(conf):{'before':s.encode(old),'after':s.encode(conf.read_bytes())}}})); oldstate=state.read_bytes()
        if scenario=='from024': cfg.write_text('{"stream_mode":"HLS"}')
        with ExitStack() as stack:
            for k,v in [('HOME',home),('APPS',apps),('CONF',conf),('STATE',state),('MODE_CONFIG',cfg)]: stack.enter_context(patch.object(s,k,v))
            stack.enter_context(patch.object(s,'compatible')); idle=stack.enter_context(patch.object(s,'require_idle',side_effect=RuntimeError('printing') if scenario=='printing' else None)); stack.enter_context(patch.object(s,'app_running',return_value=False)); stack.enter_context(patch.object(u,'stop')); stack.enter_context(patch.object(s,'loaded_camera',return_value=True)); start=stack.enter_context(patch.object(s,'start_camera'));
            if scenario=='failure': stack.enter_context(patch.object(s,'save_state',side_effect=[None,RuntimeError('disk error')]))
            if scenario=='failure':
                # mocked first save does not alter on disk; restore still required after swap
                try: u.upgrade(stage)
                except RuntimeError: pass
                else: raise AssertionError('failed write not detected')
            elif scenario=='printing':
                try: u.upgrade(stage)
                except RuntimeError: pass
                else: raise AssertionError('active print accepted')
            else:
                u.upgrade(stage)
                if scenario=='disabled': start.assert_not_called()
                else: start.assert_called_once_with(dest)
        if scenario in ('ok','from024','from026','from027','from028','from029','fromrc1','disabled'):
            assert json.loads((dest/'app.json').read_text())['version']==u.VERSION
            assert json.loads((dest/'app.json').read_text())['name']=='Anycubic Original Camera - WebSocket Bridge'
            assert (dest/('.disabled' if scenario=='disabled' else '.enabled')).exists() and (dest/'low_latency.py').exists()
            assert json.loads(state.read_text())['files'][str(conf)]['before']==s.encode(old)
            assert list(home.glob('original-camera-hls-upgrade-*/app/old.py'))
        else: assert (dest/'old.py').exists() and state.read_bytes()==oldstate
        assert conf.read_text()==s.CAMERA
        if scenario=='from024': assert json.loads(cfg.read_text())['stream_mode']=='HLS'
print('PASS: upgrade from 0.2.2, preserve original rollback and flags, failed upgrade restore, repeated install, block printing')
