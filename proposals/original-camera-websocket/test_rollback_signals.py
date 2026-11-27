"""Exercise the complete USB rollback wrapper against a temporary mock printer."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent
for scenario, preflight, rollback, mute, expected, rc in [
    ('success',0,0,False,[150,150],0),
    ('old_rc1',0,0,False,[150,150],0),
    ('failure',0,1,False,[150,150,150],1),
    ('blocked',3,0,False,[600,600],3),
    ('muted',0,0,True,[],0),
    ('beep_error',0,0,False,[],0),
]:
    with tempfile.TemporaryDirectory() as d:
        t=Path(d); home=t/'home'; app=home/'apps/39-original-camera-hls'; app.mkdir(parents=True)
        usb=t/'usb'; usb.mkdir(); stage=t/'stage'; stage.mkdir(); pwm=t/'pwm'; pwm.mkdir()
        for k,v in {'enable':'0','period':'10000','duty_cycle':'5000'}.items(): (pwm/k).write_text(v)
        cfg=app.parent/(app.name+'.config'); cfg.write_text(json.dumps({'install_sounds':'Off' if mute else 'On'}))
        (app/'app.json').write_text(json.dumps({'name':'Anycubic Original Camera - HLS Bridge' if scenario=='old_rc1' else 'Anycubic Original Camera - WebSocket Bridge','version':'1.0.0-rc1' if scenario=='old_rc1' else '1.0.0-rc2'}))
        (app/'setup.py').write_text('import sys,os\nfrom pathlib import Path\nwith open(os.environ["CALLS"],"a") as f: f.write(sys.argv[1]+"\\n")\nif sys.argv[1]=="preflight": sys.exit(int(os.environ["PRE"]))\nif int(os.environ["ROLL"]): sys.exit(int(os.environ["ROLL"]))\nPath(os.environ["CONFIG"]).unlink()\n')
        (app/'app.sh').write_text('echo stop >> "$CALLS"\n')
        tools=t/'tools.sh'; tools.write_text('KOBRA_MODEL_CODE=KS1\nbeep() { '+('return 1' if scenario=='beep_error' else 'echo "$1" >> "$TRACE"')+'; }\nusleep() { :; }\n')
        (stage/'install-signals.sh').write_text((ROOT/'install-signals.sh').read_text().replace('/useremain/rinkhals/.current/tools.sh',str(tools)).replace('/sys/class/pwm/pwmchip0/pwm0',str(pwm)))
        mounts=t/'mounts'; mounts.write_text(f'dev {usb} vfat rw 0 0\n')
        script=(ROOT/'update-rollback.sh').read_text().replace('/useremain/home/rinkhals',str(home)).replace('/useremain/update_swu',str(stage)).replace('/mnt/udisk',str(usb)).replace('/proc/mounts',str(mounts)).replace('/usr/bin/python3',sys.executable).replace('import sys, ssl, paho.mqtt.client;', 'import sys, ssl;')
        wrapper=t/'run.sh'; wrapper.write_text(script)
        env=dict(os.environ,CALLS=str(t/'calls'),TRACE=str(t/'trace'),CONFIG=str(cfg),PRE=str(preflight),ROLL=str(rollback))
        result=subprocess.run(['sh',str(wrapper)],env=env)
        assert result.returncode==rc,(scenario,result.returncode)
        got=[int(v) for v in (t/'trace').read_text().split()] if (t/'trace').exists() else []
        assert got==expected,(scenario,got)
        calls=(t/'calls').read_text().splitlines(); assert calls==(['preflight'] if preflight else ['preflight','rollback']+([] if rollback else ['stop'])),(scenario,calls)
        assert app.exists()==bool(rc),scenario
        assert (usb/'original-camera-hls-result.txt').read_text().startswith({0:'SUCCESS',1:'FAILED',3:'BLOCKED'}[rc])
        assert (usb/'original-camera-hls-install.log').exists()
print('PASS: rollback success, error, busy guard, saved mute after config deletion, beep failure and USB status')
