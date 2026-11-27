import importlib.util, tempfile, subprocess, threading, urllib.request, json, os, sys
from pathlib import Path

def load(name):
    sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
    p=Path(__file__).parent/'39-original-camera-hls'/f'{name}.py'
    spec=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
s=load('setup'); c=load('camera_hls')
original='[server]\nport: 7125\n\n[webcam Webcam]\nservice: mjpegstreamer\nstream_url: old\n\n[other]\nkey: yes\n'
new=s.section_replace(original,s.CAMERA)
assert new.count('[webcam Webcam]')==1 and '/webcam/live.m3u8' in new and '[other]\nkey: yes' in new
assert 'stream_url: old' not in new and 'port: 7125' in new
assert s.section_replace('[server]\nport: 7125\n',s.CAMERA).count('[webcam Webcam]')==1
try: s.section_replace(original+'\n[webcam Webcam]\n',s.CAMERA); raise AssertionError('duplicate accepted')
except RuntimeError: pass
with tempfile.TemporaryDirectory() as t:
    root=Path(t); f=root/'source.flv'; folder=root/'hls'; folder.mkdir()
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=320x240:r=10','-t','10','-c:v','libx264','-threads','1','-g','20','-f','flv',str(f)],check=True)
    subprocess.run(c.command(folder,ffmpeg='ffmpeg',source=str(f)),check=True)
    playlist=(folder/'live.m3u8').read_text(); assert '#EXTM3U' in playlist and 'seg-' in playlist
    with c.Server(('127.0.0.1',0),c.partial(c.Handler,directory=str(folder))) as srv:
        t=threading.Thread(target=srv.serve_forever,daemon=True); t.start()
        base='http://127.0.0.1:'+str(srv.server_port)
        with urllib.request.urlopen(base+'/live.m3u8') as res:
            assert res.headers['Content-Type']=='application/vnd.apple.mpegurl' and b'#EXTM3U' in res.read()
        segment=next(x for x in playlist.splitlines() if x.endswith('.ts'))
        with urllib.request.urlopen(base+'/'+segment) as res: assert res.headers['Content-Type']=='video/mp2t' and res.read(1)==b'\x47'
        try: urllib.request.urlopen(base+'/../setup.py'); raise AssertionError('unexpected access')
        except urllib.error.HTTPError as e: assert e.code==404
        with urllib.request.urlopen(base+'/status.json') as res:
            assert json.load(res)['version']=='1.0.0-rc2'
        with urllib.request.urlopen(base+'/status.html') as res:
            assert b'Anycubic Original Camera - WebSocket Bridge' in res.read()
        srv.shutdown(); t.join()
print('PASS: config replacement/duplicate rejection; synthetic H264 FLV -> HLS remux; HTTP manifest, segment, path restriction.')
