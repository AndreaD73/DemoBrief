# leggi-video.ps1 <file video oppure URL> [-Uscita <cartella>] [-Lingua en|it|auto] [-SenzaOcchio] [-Canali <elenco>]
# Produce <uscita>\racconto.md e <uscita>\fotogrammi\ (indice.md = orario -> png).
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Video,
    [string]$Uscita,
    [ValidateSet('', 'en', 'it', 'auto')][string]$Lingua = '',
    [switch]$SenzaOcchio,
    [string]$Canali
)
$ErrorActionPreference = 'Stop'
$py = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Error 'Strumento non installato: eseguire prima installa.ps1'; exit 2 }
Write-Host "DemoBrief $((Get-Content (Join-Path $PSScriptRoot 'version.txt') -TotalCount 1))"
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$argomenti = @((Join-Path $PSScriptRoot 'src\leggi_video.py'), $Video)
if ($Lingua) { $argomenti += @('--lingua', $Lingua) }
if ($Canali) { $argomenti += @('--canali', $Canali) }
if ($Uscita) { $argomenti += @('--uscita', $Uscita) }
if ($SenzaOcchio) { $argomenti += '--senza-occhio' }
& $py @argomenti
exit $LASTEXITCODE
