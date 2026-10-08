from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[5]
logs=root/'artifacts/product-acceptance/ui-6/self-review/fix'
commands=[('typecheck-verified',['npm','run','typecheck']),('lint-verified',['npm','run','lint']),('build-verified',['npm','run','build']),('unit-verified',['npm','test','--','--maxWorkers=2'])]
results=[]
for name,command in commands:
    with (logs/(name+'.log')).open('w') as output:
        result=subprocess.run(command,cwd=root/'web',stdout=output,stderr=subprocess.STDOUT)
    results.append(f'{name}: exit {result.returncode}')
    (logs/'frontend-exits.txt').write_text('\n'.join(results)+'\n')
raise SystemExit(1 if any(not line.endswith('exit 0') for line in results) else 0)
