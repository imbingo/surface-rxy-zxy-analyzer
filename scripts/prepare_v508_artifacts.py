"""Freeze and verify review deliverables; never zip a live installation tree."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'review_package'
INSTALLERS = ('SurfaceRxyZxyAnalyzer_Setup_V5.0.8.exe',
              'SurfaceAnalyzer_VigImageExtension_Setup_V1.2.0.exe')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    expected = os.environ['GITHUB_SHA']
    checksums = {}
    for name in INSTALLERS:
        path = ROOT/'release'/name
        checksum = path.with_suffix('.exe.sha256').read_text(encoding='utf-8-sig').split()[0]
        if digest(path) != checksum:
            raise ValueError('Installer checksum mismatch: '+name)
        checksums[name] = checksum
    manifest = read_json(ROOT/'release/update_manifest.json')
    if manifest['source_commit'] != expected or manifest['sha256'] != checksums[INSTALLERS[0]]:
        raise ValueError('Installer manifest provenance mismatch')
    qa = ROOT/'.qa'
    for name in ('frozen-v508.json','frozen-without-vig.json'):
        result = read_json(qa/name)
        if not result['ok'] or result['version'] != 'V5.0.8' or result['source_commit'] != expected:
            raise ValueError('Frozen acceptance mismatch: '+name)
    if not read_json(qa/'frozen-module.json')['ok']:
        raise ValueError('Frozen optional module check failed')
    installed = read_json(qa/'installer/installer-qa.json')
    if installed['source_commit'] != expected or not all(installed[k] for k in
            ('clean_install','uninstall','upgrade','user_data_preserved')):
        raise ValueError('Main installer acceptance failed')
    extension = read_json(qa/'extension-install/extension-installer-qa.json')
    if extension['version'] != '1.2.0' or not all(extension[k] for k in
            ('app_id_preserved','isolated_install','upgrade','frozen_vig','uninstall')):
        raise ValueError('Extension installer acceptance failed')
    tests = read_json(qa/'tests/results.json')
    if len(tests) != 11 or any(item['exit_code'] for item in tests):
        raise ValueError('Directed tests did not all pass')
    collected = 0
    for item in tests:
        folder = qa/'tests'
        xml = ET.parse(folder/(item['file']+'.xml')).getroot()
        if any(int(node.get('failures','0')) or int(node.get('errors','0'))
               for node in xml.iter('testsuite')):
            raise ValueError('Failure in JUnit output: '+item['file'])
        log = (folder/(item['file']+'.log')).read_text(encoding='utf-8',errors='replace')
        match = re.search(r'collected (\d+) item',log)
        if not match:raise ValueError('Missing test collection count: '+item['file'])
        collected += int(match.group(1))
    if DEST.exists():
        # Only our fixed staging directory inside the checkout may be replaced.
        if DEST.is_symlink() or DEST.parent != ROOT:raise ValueError('Unsafe staging path')
        shutil.rmtree(DEST)
    (DEST/'release').mkdir(parents=True)
    for path in (ROOT/'release').iterdir():
        if path.is_file() and (path.suffix in ('.exe','.sha256') or path.name=='update_manifest.json'):
            shutil.copy2(path,DEST/'release'/path.name)
    allowed = {'.json','.jsonl','.png','.csv','.npz','.log','.xml'}
    excluded = ('previous/', 'installer/installed/', 'extension-install/installed-vig/',
                'extension-install/previous-source/')
    for path in qa.rglob('*'):
        relative = path.relative_to(qa)
        if path.suffix not in allowed or relative.as_posix().startswith(excluded):continue
        if not path.is_file():continue
        target = DEST/'validation'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
    docs = DEST/'docs';docs.mkdir()
    for name in ('V5.0.8_CHANGES.md','composite-recipes.md'):
        shutil.copy2(ROOT/'docs'/name,docs/name)
    if (ROOT/'docs/v508-screenshots').is_dir():
        shutil.copytree(ROOT/'docs/v508-screenshots',docs/'v508-screenshots')
    (DEST/'SHA256SUMS.txt').write_text(''.join(f'{value}  release/{name}\n'
        for name,value in checksums.items()),encoding='ascii')
    summary = dict(version='V5.0.8',vig_version='1.2.0',source_commit=expected,
                   collected_tests=collected,installer_sha256=checksums,
                   frozen_vig=True,main_install_upgrade=True,extension_install_upgrade=True)
    (DEST/'validation-summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    # Inventory the immutable staging tree before the upload action reads it.
    inventory = {str(p.relative_to(DEST)).replace('\\','/'):dict(bytes=p.stat().st_size,sha256=digest(p))
                 for p in sorted(DEST.rglob('*')) if p.is_file()}
    (DEST/'file-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
