"""Bounded test processes, with persistent logs even if a Qt test hangs."""
import json
import os
from pathlib import Path
import subprocess
import sys

FILES = ('test_v507', 'test_vig_module', 'test_composite_workflow',
         'test_gap_registration', 'test_theme', 'test_judgment',
         'test_module_host', 'test_format_extensions')


def main():
    root = Path(__file__).resolve().parents[1]
    output = root/'.qa/tests'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for name in FILES:
        log = output/(name+'.log')
        command = [sys.executable, '-m', 'pytest', f'tests/{name}.py', '-vv',
                   '--disable-warnings', '--tb=short', '--timeout=100',
                   '--timeout-method=thread', f'--junitxml={output/name}.xml']
        print('Running '+name, flush=True)
        with log.open('w', encoding='utf-8') as stream:
            try:
                process = subprocess.run(command,cwd=root,stdout=stream,
                                         stderr=subprocess.STDOUT,timeout=360)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
                stream.write('\nSuite process exceeded 360 seconds\n')
        tail = '\n'.join(log.read_text(encoding='utf-8',errors='replace').splitlines()[-35:])
        print(tail, flush=True)
        if code:
            detail = tail.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print(f'::error title={name} failed::{detail}', flush=True)
        results.append(dict(file=name,exit_code=code))
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary,'a',encoding='utf-8') as stream:
                stream.write(f'### {name}: exit {code}\n\n```text\n{tail}\n```\n\n')
    (output/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    return int(any(item['exit_code'] for item in results))


if __name__ == '__main__':
    raise SystemExit(main())
