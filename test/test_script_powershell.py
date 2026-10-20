"""The PowerShell scripts must parse in Windows PowerShell 5.1 (a stray quote broke installa.ps1 in 0.7.13/0.7.14)."""
import os
import shutil
import subprocess

import pytest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = ('$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('
          '$args[0],[ref]$null,[ref]$e);if($e){$e|ForEach-Object{$_.Message};exit 1}')


@pytest.mark.skipif(shutil.which('powershell') is None, reason='Windows PowerShell not available')
@pytest.mark.parametrize('nome', ['installa.ps1', 'leggi-video.ps1'])
def test_script_ps1_senza_errori_di_sintassi(nome):
    esito = subprocess.run(['powershell', '-NoProfile', '-Command', SCRIPT.replace('$args[0]', f"'{os.path.join(RADICE, nome)}'")],
                           capture_output=True, text=True)
    assert esito.returncode == 0, esito.stdout
