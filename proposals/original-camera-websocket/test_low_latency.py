import sys, tempfile, subprocess, threading, time, io, json, socket, base64, struct, os
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'39-original-camera-hls'))
import low_latency as l, camera_hls as c, setup as s
from websocket import create_connection
with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'source.flv'; out=Path(d)/'out.h264'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=s=160x120:r=5','-t','4','-c:v','libx264','-threads','1','-g','5','-bf','0','-f','flv',str(p)],check=True)
    frames=[]
    with p.open('rb') as f:
        it=l.access_units(f)
        try:
            while True: frames.append(next(it))
        except EOFError: pass
    assert len(frames)==20 and sum(k for k,_ in frames)==4
    assert frames[0][0] and b'\x00\x00\x00\x01\x67' in frames[0][1]
    with c.LowServer(('127.0.0.1',0),l.WSHandler) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        ws=create_connection('ws://127.0.0.1:'+str(server.server_port)+'/h264',timeout=3,origin='http://127.0.0.1')
        # Ensure new viewers do not receive a delta frame before an IDR.
        l.HUB.send(*frames[1]); received=[]
        for k,data in frames:
            l.HUB.send(k,data); received.append(ws.recv())
        assert received==[data for _,data in frames]
        ws.ping(b'ping'); opcode,frame=ws.recv_data_frame(control_frame=True); assert opcode==10 and frame.data==b'ping'
        ws.close(); server.shutdown(); thread.join()
    out.write_bytes(b''.join(received))
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-xerror','-f','h264','-i',str(out),'-f','null','-'],check=True)
    # Reject truncated/malformed configuration and tag lengths.
    for data in (b'junk',b'\x01'*7):
        try: l.avcc(data)
        except ValueError: pass
        else: raise AssertionError('bad AVC accepted')
    h=l.Hub(); q=h.subscribe()
    for _ in range(10): h.send(False,b'x')
    assert q.qsize()<=4
    h.reset(); assert q.get_nowait() is None
print('PASS: 20 FLV AVC frames, IDR/SPS/PPS, real RFC6455 binary frames, ping/pong, ffmpeg decodes received stream, bounded queue')

with tempfile.TemporaryDirectory() as d:
    root=Path(d); state=root/'state.json'; conf=root/'moonraker.custom.conf'; mode=root/'app.config'
    base=b'[server]\nport: 7125\n'; original=s.section_replace(base.decode(),s.CAMERA).encode(); conf.write_bytes(original)
    state.write_text(json.dumps({'phase':'ready','files':{str(conf):{'before':s.encode(base),'after':s.encode(original)}}}))
    with patch.object(s,'STATE',state),patch.object(s,'CONF',conf),patch.object(s,'MODE_CONFIG',mode),patch.object(s,'lan_ip',return_value='192.168.8.239'),patch.object(s,'require_idle'),patch.object(s,'restart_moonraker') as restart:
        assert s.requested_mode()=='Low Latency' # fresh install, no properties file
        mode.write_text('{}'); assert s.requested_mode()=='Low Latency'
        mode.write_text('{"stream_mode":"HLS"}'); assert s.requested_mode()=='HLS'
        mode.unlink(); assert s.requested_mode()=='Low Latency'
        assert json.loads((Path(__file__).parent/'39-original-camera-hls/app.json').read_text())['properties']['stream_mode']['default']=='Low Latency'
        mode.write_text('{"stream_mode":"Low Latency"}')
        s.apply_mode(s.requested_mode()); assert b'ws://192.168.8.239:8086/h264' in conf.read_bytes(); assert restart.call_count==1
        assert json.loads(state.read_text())['files'][str(conf)]['before']==s.encode(base)
        s.fallback_hls(); assert b'/webcam/live.m3u8' in conf.read_bytes(); assert s.requested_mode()=='HLS'; assert restart.call_count==2
        with patch.object(s,'require_idle',side_effect=RuntimeError('printing')):
            try: s.apply_mode('Low Latency')
            except RuntimeError: pass
            else: raise AssertionError('Mode switch during print accepted')
            assert b'hlsstream' in conf.read_bytes()
print('PASS: mode changes, HLS fallback, original backup retained, no switch while printing')
