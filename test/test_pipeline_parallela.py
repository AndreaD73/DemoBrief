# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import time

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)

import leggi_video


def _avviatore_simulato(tmp_path, durate, fallisce=None, processi=None):
    processi = processi if processi is not None else []
    eventi = tmp_path / 'eventi.txt'

    def avvia(script, *args, nome=None, lavoro=None):
        fase = nome or os.path.splitext(script)[0]
        dipendenze = (
            "deps = os.path.exists(os.path.join(lavoro, 'movimento.ok')) "
            "and os.path.exists(os.path.join(lavoro, 'ocr_letture.ok'))"
            if fase == 'ocr_azione'
            else 'deps = True'
        )
        codice = (
            "import os,sys,time\n"
            "lavoro,fase,eventi,durata,fallisce=sys.argv[1:]\n"
            + dipendenze
            + "\n"
            "with open(eventi,'a',encoding='utf-8') as f:\n"
            " f.write('inizio:%s:deps=%s\\n' % (fase, deps))\n"
            "time.sleep(float(durata))\n"
            "if fallisce == '1':\n"
            " sys.exit(7)\n"
            "open(os.path.join(lavoro, fase + '.ok'),'w').write('ok')\n"
            "with open(eventi,'a',encoding='utf-8') as f:\n"
            " f.write('fine:%s\\n' % fase)\n"
        )
        registro = open(
            os.path.join(lavoro, fase + '.log'),
            'w',
            encoding='utf-8',
        )
        processo = subprocess.Popen(
            [
                sys.executable,
                '-c',
                codice,
                str(lavoro),
                fase,
                str(eventi),
                str(durate[fase]),
                '1' if fase == fallisce else '0',
            ],
            stdout=registro,
            stderr=subprocess.STDOUT,
        )
        processi.append(processo)
        return processo, registro, fase

    return avvia, eventi


def test_parallelo_dura_meno_della_somma_e_rispetta_dipendenze(
    monkeypatch,
    tmp_path,
):
    durate = {
        'movimento': 0.30,
        'trascrivi': 0.30,
        'ocr_letture': 0.30,
        'ocr_schermate': 0.30,
        'ocr_azione': 0.05,
    }
    avvia, eventi = _avviatore_simulato(tmp_path, durate)
    monkeypatch.setattr(leggi_video, 'avvia', avvia)
    monkeypatch.setattr(leggi_video, 'FIGLI', [])

    iniziato = time.monotonic()
    leggi_video.esegui_fasi_parallele(
        'video.mp4',
        str(tmp_path),
        'it',
        1,
    )
    durata = time.monotonic() - iniziato
    righe = eventi.read_text(encoding='utf-8').splitlines()

    assert durata < sum(durate.values()) - 0.40
    assert 'inizio:ocr_azione:deps=True' in righe
    assert righe.index('fine:movimento') < righe.index(
        'inizio:ocr_azione:deps=True'
    )
    assert righe.index('fine:ocr_letture') < righe.index(
        'inizio:ocr_azione:deps=True'
    )


def test_sequenza_precedente_resterebbe_nello_stesso_ordine(
    monkeypatch,
    tmp_path,
):
    ordine = []

    def avvia(script, *args, nome=None, lavoro=None):
        fase = nome or os.path.splitext(script)[0]
        ordine.append(fase)

        class Processo:
            pid = 1

            def wait(self, timeout=None):
                return 0

        class Registro:
            def close(self):
                pass

        return Processo(), Registro(), fase

    monkeypatch.setattr(leggi_video, 'avvia', avvia)

    leggi_video.attendi(
        leggi_video.avvia('movimento.py', 'v', 'l', lavoro=str(tmp_path)),
        str(tmp_path),
    )
    leggi_video.attendi(
        leggi_video.avvia('trascrivi.py', 'v', 'l', 'it', lavoro=str(tmp_path)),
        str(tmp_path),
    )
    leggi_video.attendi(
        leggi_video.avvia(
            'ocr.py', 'letture', 'v', 'l',
            nome='ocr_letture', lavoro=str(tmp_path),
        ),
        str(tmp_path),
    )
    leggi_video.attendi(
        leggi_video.avvia(
            'ocr.py', 'azione', 'v', 'l',
            nome='ocr_azione', lavoro=str(tmp_path),
        ),
        str(tmp_path),
    )
    leggi_video.attendi(
        leggi_video.avvia(
            'ocr.py', 'schermate', 'v', 'l',
            nome='ocr_schermate', lavoro=str(tmp_path),
        ),
        str(tmp_path),
    )

    assert ordine == [
        'movimento',
        'trascrivi',
        'ocr_letture',
        'ocr_azione',
        'ocr_schermate',
    ]


def test_fallimento_termina_le_altre_fasi(monkeypatch, tmp_path):
    durate = {
        'movimento': 0.05,
        'trascrivi': 3.0,
        'ocr_letture': 3.0,
        'ocr_schermate': 3.0,
        'ocr_azione': 0.05,
    }
    processi = []
    avvia, _ = _avviatore_simulato(
        tmp_path,
        durate,
        fallisce='movimento',
        processi=processi,
    )
    monkeypatch.setattr(leggi_video, 'avvia', avvia)
    monkeypatch.setattr(leggi_video, 'FIGLI', processi)

    with pytest.raises(SystemExit) as errore:
        leggi_video.esegui_fasi_parallele(
            'video.mp4',
            str(tmp_path),
            'it',
            1,
        )

    assert isinstance(errore.value.code, str)
    assert 'Passo "movimento" fallito' in errore.value.code
    assert all(p.poll() is not None for p in processi)


def test_memoria_bassa_forza_la_sequenza(monkeypatch, capsys):
    monkeypatch.setattr(leggi_video, 'memoria_libera_gb', lambda: 5.5)

    assert leggi_video.usa_fasi_parallele({'parallelo': True}) is False
    assert 'memoria libera 5.5 GB: fasi in sequenza' in capsys.readouterr().out


def test_memoria_non_disponibile_non_disabilita_il_parallelo(monkeypatch):
    monkeypatch.setattr(leggi_video, 'memoria_libera_gb', lambda: None)

    assert leggi_video.usa_fasi_parallele({'parallelo': True}) is True
    assert leggi_video.usa_fasi_parallele({'parallelo': False}) is False
