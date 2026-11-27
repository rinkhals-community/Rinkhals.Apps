import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as td:
    temp = Path(td); pwm = temp/'pwm'; pwm.mkdir()
    for key,val in {'enable':'0','period':'10000','duty_cycle':'5000'}.items(): (pwm/key).write_text(val)
    config=temp/'camera.config'; tools=temp/'tools.sh'; trace=temp/'beeps.txt'
    tools.write_text('KOBRA_MODEL_CODE=KS1\nbeep() { echo "$1" >> "$TRACE"; }\nusleep() { :; }\n')
    def run(code, expected, config_value=None, enabled='0', duty='5000', model='KS1'):
        (pwm/'enable').write_text(enabled); (pwm/'duty_cycle').write_text(duty)
        tools.write_text(f'KOBRA_MODEL_CODE={model}\nbeep() {{ echo "$1" >> "$TRACE"; }}\nusleep() {{ :; }}\n')
        trace.unlink(missing_ok=True); config.unlink(missing_ok=True)
        if config_value is not None: config.write_text(json.dumps(config_value))
        env=dict(os.environ,TRACE=str(trace))
        subprocess.run(['sh','-c','. "$1"; camera_signal "$2" "$3" "$4" "$5" "$6"','sh',str(ROOT/'install-signals.sh'),str(code),str(config),sys.executable,str(tools),str(pwm)],env=env,check=True)
        got=[int(v) for v in trace.read_text().split()] if trace.exists() else []
        assert got==expected,(code,got,expected)
        assert (pwm/'enable').read_text()==enabled
        assert (pwm/'duty_cycle').read_text()==duty
    run(0,[150,150]); run(1,[150,150,150]); run(2,[150,600]); run(3,[600,600]); run(99,[150,150,150])
    run(0,[],{'install_sounds':'Off'}); run(0,[150,150],{'stream_mode':'HLS'}); run(0,[],enabled='1'); run(0,[],duty='0'); run(0,[],model='KS1M')
    tools.write_text('KOBRA_MODEL_CODE=KS1\nbeep() { return 1; }\nusleep() { :; }\n')
    (pwm/'enable').write_text('0'); (pwm/'duty_cycle').write_text('5000'); config.unlink(missing_ok=True)
    # Failure in the beep helper must never replace a real install result.
    proc=subprocess.run(['sh','-c','. "$1"; notify() { rc=$?; trap - EXIT; camera_signal "$rc" "$2" "$3" "$4" "$5" || :; exit "$rc"; }; trap \'notify "$@"\' EXIT; exit 3','sh',str(ROOT/'install-signals.sh'),str(config),sys.executable,str(tools),str(pwm)])
    assert proc.returncode==3,proc.returncode
print('PASS: all signal patterns, disabled/busy/muted/unsupported guards and preservation of installer exit code.')
