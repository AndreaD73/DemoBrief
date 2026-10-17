import os
import sys
import types

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RADICE, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ocr


def _inizializza(monkeypatch, configurazione, provider):
    costruzioni = []
    preload = []

    class RapidOCRFinto:
        def __init__(self, **kwargs):
            costruzioni.append(kwargs)

        def __call__(self, *args, **kwargs):
            return [], None

    modulo_rapidocr = types.ModuleType('rapidocr_onnxruntime')
    modulo_rapidocr.RapidOCR = RapidOCRFinto
    modulo_onnxruntime = types.ModuleType('onnxruntime')
    modulo_onnxruntime.get_available_providers = lambda: list(provider)
    modulo_onnxruntime.preload_dlls = lambda: preload.append(True)

    monkeypatch.setitem(sys.modules, 'rapidocr_onnxruntime', modulo_rapidocr)
    monkeypatch.setitem(sys.modules, 'onnxruntime', modulo_onnxruntime)
    monkeypatch.setattr(ocr.comune, 'config', lambda: dict(configurazione))
    monkeypatch.setattr(ocr.tempi, 'configura', lambda *args, **kwargs: None)
    monkeypatch.setattr(ocr.tempi, 'strumenta_rapidocr', lambda motore: motore)
    monkeypatch.setenv('AD_MOVIOLA_TEMPI_FASE', 'test_ocr')
    monkeypatch.setenv('AD_MOVIOLA_TEMPI_LAVORO', os.path.dirname(__file__))
    ocr._eng = None

    ocr._init()
    return costruzioni, preload


def test_prova_cuda_fallita_ripiega_sul_processore(monkeypatch, capsys):
    costruzioni = []
    preload = []

    class RapidOCRFinto:
        def __init__(self, **kwargs):
            self.cuda = kwargs.get('det_use_cuda', False)
            costruzioni.append(kwargs)

        def __call__(self, *args, **kwargs):
            if self.cuda:
                raise RuntimeError('CUDA non funzionante')
            return [], None

    modulo_rapidocr = types.ModuleType('rapidocr_onnxruntime')
    modulo_rapidocr.RapidOCR = RapidOCRFinto
    modulo_onnxruntime = types.ModuleType('onnxruntime')
    modulo_onnxruntime.get_available_providers = lambda: [
        'CUDAExecutionProvider',
        'CPUExecutionProvider',
    ]
    modulo_onnxruntime.preload_dlls = lambda: preload.append(True)

    monkeypatch.setitem(sys.modules, 'rapidocr_onnxruntime', modulo_rapidocr)
    monkeypatch.setitem(sys.modules, 'onnxruntime', modulo_onnxruntime)
    monkeypatch.setattr(ocr.comune, 'config', lambda: {'ocr_gpu': True})
    monkeypatch.setattr(ocr.tempi, 'configura', lambda *args, **kwargs: None)
    monkeypatch.setattr(ocr.tempi, 'strumenta_rapidocr', lambda motore: motore)
    monkeypatch.setenv('AD_MOVIOLA_TEMPI_FASE', 'test_ocr')
    monkeypatch.setenv('AD_MOVIOLA_TEMPI_LAVORO', os.path.dirname(__file__))
    ocr._eng = None

    ocr._init()

    assert costruzioni == [
        {
            'det_use_cuda': True,
            'rec_use_cuda': True,
            'cls_use_cuda': True,
        },
        {
            'intra_op_num_threads': 1,
            'inter_op_num_threads': 1,
        },
    ]
    assert preload == [True]
    assert capsys.readouterr().out == (
        'OCR su processore (CUDA non funzionante)\n'
    )


def test_ocr_gpu_disattivata_usa_processore(monkeypatch, capsys):
    costruzioni, preload = _inizializza(
        monkeypatch,
        {'ocr_gpu': False},
        ['CUDAExecutionProvider', 'CPUExecutionProvider'],
    )

    assert costruzioni == [{
        'intra_op_num_threads': 1,
        'inter_op_num_threads': 1,
    }]
    assert preload == []
    assert capsys.readouterr().out == 'OCR su processore\n'


def test_provider_cuda_assente_usa_processore(monkeypatch, capsys):
    costruzioni, preload = _inizializza(
        monkeypatch,
        {'ocr_gpu': True},
        ['CPUExecutionProvider'],
    )

    assert costruzioni == [{
        'intra_op_num_threads': 1,
        'inter_op_num_threads': 1,
    }]
    assert preload == []
    assert capsys.readouterr().out == 'OCR su processore\n'
