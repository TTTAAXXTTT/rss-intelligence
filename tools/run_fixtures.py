"""Run fixture suites; Linux is required for POSIX permission/lock checks."""
from pathlib import Path
import os, subprocess, sys
root=Path(__file__).resolve().parents[1]
tests=['init-fixture-test.py','snapshot-fixture-test.py','daily-fixture-test.py','extract-agent-envelope-fixture-test.py','host-finalize-fixture-test.py','host-review-fixture-test.py','host-publish-v53-fixture-test.py','host-translate-v53-fixture-test.py','source-health-v53-fixture-test.py']
posix={'host-review-fixture-test.py','host-publish-v53-fixture-test.py','host-translate-v53-fixture-test.py'}
failed=[];skipped=[]
env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
for name in tests:
    if os.name=='nt' and name in posix:
        print('SKIP Linux permission semantics required:',name);skipped.append(name);continue
    args=[sys.executable,str(root/'00-preflight'/name)]
    if name in tests[:3]:args.append(str(root))
    if subprocess.run(args,env=env).returncode:failed.append(name)
print({'suites':len(tests),'passed':len(tests)-len(failed)-len(skipped),'skipped':skipped,'failed':failed})
raise SystemExit(bool(failed))
