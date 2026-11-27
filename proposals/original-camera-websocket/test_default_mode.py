import sys, tempfile, json
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import setup as s
manifest=json.loads((Path(__file__).parent/'39-original-camera-hls/app.json').read_text())
assert manifest['properties']['stream_mode']['default']=='Low Latency'
for override in (None,'HLS','Low Latency'):
    with tempfile.TemporaryDirectory() as d:
        home=Path(d); apps=home/'apps'; apps.mkdir(); bridge=apps/'cloud2lan-bridge'; bridge.mkdir(); dest=apps/s.APP; dest.mkdir()
        conf=home/'moonraker.custom.conf'; conf.write_text('[server]\nport: 7125\n'); original=conf.read_bytes(); state=home/'state.json'; cfg=apps/(s.APP+'.config')
        if override is not None: cfg.write_text(json.dumps({'stream_mode':override}))
        with ExitStack() as stack:
            for k,v in [('HOME',home),('APPS',apps),('CONF',conf),('STATE',state),('MODE_CONFIG',cfg)]: stack.enter_context(patch.object(s,k,v))
            for k in ('compatible','require_idle','start_bridge','set_mode'): stack.enter_context(patch.object(s,k))
            stack.enter_context(patch.object(s,'lan_mode',return_value=True)); stack.enter_context(patch.object(s,'lan_ip',return_value='192.168.8.239')); stack.enter_context(patch.object(s,'app_running',return_value=True)); stack.enter_context(patch.object(s.dependency,'install_if_missing',return_value=(bridge,'1.2',False)))
            restart=stack.enter_context(patch.object(s,'restart_moonraker')); start=stack.enter_context(patch.object(s,'start_camera'))
            expected=override or 'Low Latency'
            assert s.requested_mode()==expected
            s.install(); start.assert_called_once_with(dest)
            assert s.camera_value(conf.read_text(),'service')==('hlsstream' if expected=='HLS' else 'jmuxer-stream')
            assert restart.call_args.args==(s.selected_camera(expected),)
            assert json.loads(state.read_text())['files'][str(conf)]['before']==s.encode(original)
            s.fallback_hls(); assert s.requested_mode()=='HLS'; assert s.camera_value(conf.read_text(),'service')=='hlsstream'
            assert json.loads(state.read_text())['files'][str(conf)]['before']==s.encode(original)
print('PASS: new default, fresh setup selects correct mode, explicit HLS preserved, persistent HLS fallback and original backup')
