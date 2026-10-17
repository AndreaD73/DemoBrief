# -*- coding: utf-8 -*-
import os
import sys

import pytest


SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)

import leggi_video


def test_canale_sconosciuto_esce_due_prima_della_elaborazione(monkeypatch, capsys):
    chiamate = []

    def vietata(*args, **kwargs):
        chiamate.append((args, kwargs))
        raise AssertionError("L'elaborazione non deve essere chiamata")

    monkeypatch.setattr(
        sys,
        "argv",
        ["leggi_video.py", "video-inesistente.mp4", "--canali", "sconosciuto"],
    )
    monkeypatch.setattr(leggi_video.comune, "config", vietata)
    monkeypatch.setattr(leggi_video, "scarica", vietata)
    monkeypatch.setattr(leggi_video, "avvia", vietata)
    monkeypatch.setattr(leggi_video, "fotogrammi", vietata)

    with pytest.raises(SystemExit) as errore:
        leggi_video.main()

    cattura = capsys.readouterr()
    assert errore.value.code == 2
    assert cattura.out == ""
    assert cattura.err == (
        "Canale non valido: sconosciuto. "
        "Valori ammessi: cursore, stato, stile, risultato.\n"
    )
    assert chiamate == []
