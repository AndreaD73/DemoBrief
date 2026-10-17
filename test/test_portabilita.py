# -*- coding: utf-8 -*-
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)

import canale_stile
import comune


def test_trova_linux_usa_which_senza_exe(monkeypatch):
    richieste = []

    def which(nome):
        richieste.append(nome)
        return "/usr/local/bin/" + nome

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(comune.shutil, "which", which)

    assert comune._trova("ffmpeg") == "/usr/local/bin/ffmpeg"
    assert richieste == ["ffmpeg"]
    assert not richieste[0].endswith(".exe")


def test_ram_picco_linux_non_legge_windll(monkeypatch):
    class CtypesSentinella:
        @property
        def windll(self):
            raise AssertionError("ctypes.windll non deve essere letto su Linux")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "ctypes", CtypesSentinella())

    valore = canale_stile.ram_picco_mb()
    assert valore is None or isinstance(valore, (int, float))
