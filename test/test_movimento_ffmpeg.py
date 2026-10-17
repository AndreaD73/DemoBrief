# -*- coding: utf-8 -*-
import os
import sys

import cv2
import numpy as np
import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import comune
import movimento


def _crea_video(percorso, fps):
    larghezza, altezza = 320, 240
    scrittore = cv2.VideoWriter(
        str(percorso),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (larghezza, altezza),
    )
    assert scrittore.isOpened()
    for indice in range(64):
        fotogramma = np.zeros(
            (altezza, larghezza, 3), dtype=np.uint8
        )
        fotogramma[:, :] = (
            (indice * 3) % 256,
            (indice * 5) % 256,
            (indice * 7) % 256,
        )
        cv2.rectangle(
            fotogramma,
            (indice * 4 % 250, 30),
            (indice * 4 % 250 + 60, 100),
            (240, 180, 90),
            -1,
        )
        cv2.circle(
            fotogramma,
            (160, 40 + indice * 2 % 160),
            18,
            (20, 230, 140),
            -1,
        )
        scrittore.write(fotogramma)
    scrittore.release()


@pytest.mark.parametrize(("fps", "step_atteso"), [(10.0, 1), (30.0, 3)])
def test_ffmpeg_identico_a_cv2(tmp_path, monkeypatch, capsys, fps, step_atteso):
    video = tmp_path / ("step_%d.mp4" % step_atteso)
    _crea_video(video, fps)
    monkeypatch.setattr(
        comune, "config", lambda: {"decodifica_ffmpeg": True}
    )

    color_ffmpeg, gray_ffmpeg, dt_ffmpeg = movimento.load(str(video))
    color_cv2, gray_cv2, dt_cv2 = movimento._load_cv2(str(video))

    assert len(color_ffmpeg) == 1
    assert len(color_cv2) == 1
    assert np.array_equal(color_ffmpeg[0], color_cv2[0])
    assert np.array_equal(np.stack(gray_ffmpeg), np.stack(gray_cv2))
    assert dt_ffmpeg == dt_cv2
    assert round(fps / movimento.FPS) == step_atteso
    assert "decodifica ffmpeg" in capsys.readouterr().out


def test_ffmpeg_assente_ripiega_su_cv2(tmp_path, monkeypatch, capsys):
    video = tmp_path / "ripiego.mp4"
    _crea_video(video, 30.0)
    monkeypatch.setattr(
        comune, "config", lambda: {"decodifica_ffmpeg": True}
    )

    def ffmpeg_assente():
        raise FileNotFoundError("ffmpeg simulato assente")

    monkeypatch.setattr(comune, "ffmpeg", ffmpeg_assente)

    color_ripiego, gray_ripiego, dt_ripiego = movimento.load(str(video))
    color_cv2, gray_cv2, dt_cv2 = movimento._load_cv2(str(video))

    assert np.array_equal(color_ripiego[0], color_cv2[0])
    assert np.array_equal(np.stack(gray_ripiego), np.stack(gray_cv2))
    assert dt_ripiego == dt_cv2
    assert (
        "decodifica cv2 (ffmpeg simulato assente)"
        in capsys.readouterr().out
    )


def test_decodifica_ffmpeg_predefinita_attiva():
    assert comune.PREDEFINITI["decodifica_ffmpeg"] is True
