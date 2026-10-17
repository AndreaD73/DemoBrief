# Crea l'ambiente virtuale dello strumento e scarica i modelli richiesti.
# Uso:  powershell -ExecutionPolicy Bypass -File installa.ps1 [-SenzaModelli] [-InstallaSkill]
param([switch]$SenzaModelli, [switch]$InstallaSkill)
$ErrorActionPreference = 'Stop'
$qui = $PSScriptRoot
$venv = Join-Path $qui 'venv'
$py = Join-Path $venv 'Scripts\python.exe'

function Passo($m) { Write-Host ('==> ' + $m) -ForegroundColor Cyan }

# 1. Python 3.10-3.12 (le ruote di onnxruntime/opencv sono testate su queste versioni)
Passo 'Cerco Python 3.10-3.12'
$base = $null
$candidati = @()
$ErrorActionPreference = 'Continue'   # PowerShell 5.1: l'errore su stderr di "py" non deve fermare lo script
foreach ($v in '3.12', '3.11', '3.10') {
    $c = Get-Command py -ErrorAction SilentlyContinue
    if ($c) {
        $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p) { $candidati += $p.Trim() }
    }
}
$c = Get-Command python -ErrorAction SilentlyContinue
if ($c) { $candidati += $c.Source }
foreach ($p in $candidati) {
    if (-not (Test-Path $p)) { continue }
    $ver = (& $p -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($ver -in '3.10', '3.11', '3.12') { $base = $p; break }
}
$ErrorActionPreference = 'Stop'
if (-not $base) { throw 'Serve Python 3.10, 3.11 o 3.12 (https://www.python.org/downloads/). Non trovato.' }
Write-Host ('    ' + $base)

# 2. ffmpeg (programma esterno)
Passo 'Controllo ffmpeg'
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff -and -not (Test-Path (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'))) {
    Write-Host '    ffmpeg assente: provo con winget'
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Installare ffmpeg a mano (https://ffmpeg.org) e rilanciare.' }
}

# 3. venv
Passo 'Creo il venv'
if (-not (Test-Path $py)) { & $base -m venv $venv; if ($LASTEXITCODE -ne 0) { throw 'Creazione venv fallita' } }
& $py -m pip install --upgrade pip | Out-Null

Passo 'Installo le dipendenze (versioni fissate)'
& $py -m pip install -r (Join-Path $qui 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install fallito' }
$gpu = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($gpu) {
    Passo 'Scheda NVIDIA trovata: installo il runtime GPU e le librerie CUDA'
    & $py -m pip uninstall -y onnxruntime
    & $py -m pip install -r (Join-Path $qui 'requirements-gpu.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Runtime GPU non installato: reinstallo onnxruntime e uso il processore.'
        & $py -m pip install onnxruntime==1.31.0
        if ($LASTEXITCODE -ne 0) { throw 'Ripristino di onnxruntime fallito' }
    }
} else {
    Write-Host '    Nessuna scheda NVIDIA: uso il processore (piu'' lento).'
}

# 4. modelli (una volta sola)
if (-not $SenzaModelli) {
    Passo 'Scarico il modello vocale (whisper) e il vocabolario dei token'
    $env:PYTHONUTF8 = '1'
    $env:TIKTOKEN_CACHE_DIR = Join-Path $qui 'cache'
    & $py -I -c "import sys;sys.path.insert(0,sys.argv[1]);import comune;from faster_whisper import download_model;download_model(comune.config()['whisper']['modello'])" (Join-Path $qui 'src')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Modello whisper non scaricato: sara'' scaricato al primo uso.' }
    & $py -I -c "import tiktoken;tiktoken.get_encoding('cl100k_base')"
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Vocabolario token non scaricato: i token saranno stimati a caratteri/4.' }
}

# 5. occhio (opzionale)
Passo 'Controllo l''occhio (Ollama)'
$cfg = Get-Content (Join-Path $qui 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
try {
    $t = Invoke-RestMethod -Uri ($cfg.ollama.indirizzo.TrimEnd('/') + '/api/tags') -TimeoutSec 5
    if ($t.models.name -contains $cfg.ollama.modello) { Write-Host ('    OK: ' + $cfg.ollama.modello) }
    else { Write-Warning ('Modello mancante: eseguire  ollama pull ' + $cfg.ollama.modello + '  (senza, il racconto esce senza righe VISTA).') }
} catch {
    Write-Warning 'Ollama non risponde: il racconto uscira'' senza righe VISTA (installare Ollama per averle).'
}
if ($InstallaSkill) {
    Passo 'Installo la skill per Claude Code (cartella utente)'
    $dest = Join-Path $env:USERPROFILE '.claude\skills\leggi-video'
    New-Item -ItemType Directory -Force $dest | Out-Null
    $s = [IO.File]::ReadAllText((Join-Path $qui 'skill\SKILL.md'), [Text.Encoding]::UTF8).Replace('{{STRUMENTO}}', $qui)
    [IO.File]::WriteAllText((Join-Path $dest 'SKILL.md'), $s, (New-Object Text.UTF8Encoding($false)))
    Write-Host ('    ' + $dest)
}
Write-Host ''
Write-Host 'Fatto. Uso:  .\leggi-video.ps1 <file video oppure URL>' -ForegroundColor Green
