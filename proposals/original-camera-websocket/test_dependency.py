import hashlib, io, json, tarfile, zipfile, tempfile, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import dependency as d
import setup as s
import camera_hls as c

def fixture(unsafe=False):
    pack=io.BytesIO()
    with tarfile.open(fileobj=pack,mode='w:gz') as t:
        files={'./cloud2lan-bridge/app.json':json.dumps({'version':'1.2','properties':{'lan_mode':{}}}).encode(), './cloud2lan-bridge/app.sh':b'#!/bin/sh\n', './cloud2lan-bridge/mode_watchdog.py':b'# test\n','./cloud2lan-bridge/cloud2lan-supervisor.sh':b'#!/bin/sh\n','./update.sh':b'NEVER EXECUTE'}
        if unsafe: files['./cloud2lan-bridge/../../outside']=b'bad'
        for name,data in files.items():
            info=tarfile.TarInfo(name); info.size=len(data); t.addfile(info,io.BytesIO(data))
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as z: z.writestr('update_swu/setup.tar.gz',pack.getvalue())
    return result.getvalue()
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); data=fixture(); digest=hashlib.sha256(data).hexdigest()
    dest=root/'dest'; dest.mkdir(); d.unpack(data,dest,digest)
    assert d.check(dest)=='1.2' and not (dest/'update.sh').exists()
    bad=fixture(True)
    for payload,sha in ((data,'0'*64),(bad,hashlib.sha256(bad).hexdigest())):
        other=root/'other'; other.mkdir(exist_ok=True)
        try: d.unpack(payload,other,sha); raise AssertionError('unsafe package accepted')
        except RuntimeError: pass
    assert not (root/'outside').exists()
    # Already installed dependency must not use the network.
    apps=root/'apps'; apps.mkdir(); dest.rename(apps/d.NAME)
    with patch('urllib.request.urlopen',side_effect=AssertionError('unexpected network')):
        found,version,added=d.install_if_missing(apps,root/'builtin'); assert version=='1.2' and not added
    manifest=found/'app.json'; manifest.write_text(json.dumps({'version':'1.1','properties':{'lan_mode':{}}}))
    try: d.check(found); raise AssertionError('old dependency accepted')
    except RuntimeError: pass
with patch.object(s,'printer_idle',return_value=False):
    try: s.require_idle(); raise AssertionError('active/unknown print accepted')
    except RuntimeError: pass
# Recovery sequence: guard is checked before modifying bridge configuration.
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); bridge=root/'cloud2lan-bridge'; bridge.mkdir(); mode=root/'mode'; mode.write_text('cloud')
    with patch.object(s,'MODE',mode),patch.object(s,'printer_idle',return_value=False):
        try: s.set_mode(bridge,True); raise AssertionError('unsafe switch')
        except RuntimeError: pass
        assert not s.bridge_config(bridge).exists()
    def fake_atomic(path,data,private=False):
        path.write_bytes(data); mode.write_text('lan')
    with patch.object(s,'MODE',mode),patch.object(s,'printer_idle',return_value=True),patch.object(s,'start_bridge'),patch.object(s.time,'sleep'),patch.object(s,'atomic',side_effect=fake_atomic):
        s.set_mode(bridge,True); assert json.loads(s.bridge_config(bridge).read_text())['lan_mode']=='True'
    assert s.CAMERA.count('[webcam Webcam]')==1
print('PASS: dependency archive extraction, checksum/traversal rejection, installed/old dependency, print guard and mocked LAN repair.')
