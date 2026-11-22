"""Installer QA resolves the registered Inno filename instead of assuming 000."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name=='nt','Windows installer QA')
class ReleaseInstallerTests(unittest.TestCase):
    def resolve(self,root,command):
        shell=shutil.which('pwsh') or shutil.which('powershell')
        self.assertIsNotNone(shell)
        helper=Path(__file__).resolve().parents[1]/'scripts'/'release_install_helpers.ps1'
        quote=lambda value:"'"+str(value).replace("'","''")+"'"
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'check.ps1'
            script.write_text("$ErrorActionPreference='Stop'\n. "+quote(helper)+"\nResolve-ReleaseUninstaller -InstallRoot "+quote(root)+" -UninstallCommand "+quote(command),encoding='utf-8-sig')
            return subprocess.run([shell,'-NoProfile','-File',str(script)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',timeout=20)

    def test_registered_nonzero_uninstaller_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'Install with spaces';root.mkdir()
            for name in ('unins000.exe','unins001.exe','unins012.exe'):
                path=root/name;path.touch()
                result=self.resolve(root,'"'+str(path)+'"')
                self.assertEqual(result.returncode,0,result.stderr)
                # Hosted Windows TEMP may use RUNNER~1 while PowerShell
                # canonicalizes to runneradmin. Assert file identity, not spelling.
                self.assertTrue(os.path.samefile(result.stdout.strip(),path))

    def test_outside_directory_and_other_executables_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'installed';root.mkdir()
            outsider=Path(directory)/'unins001.exe';outsider.touch()
            other=root/'another.exe';other.touch()
            for path in (outsider,other,root/'unins999.exe'):
                result=self.resolve(root,'"'+str(path)+'"')
                self.assertNotEqual(result.returncode,0)

    def test_additional_arguments_are_not_interpreted_as_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'unins001.exe';path.touch()
            result=self.resolve(root,'"'+str(path)+'"; Write-Output unexpected')
            self.assertNotEqual(result.returncode,0)
