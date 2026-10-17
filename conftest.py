# -*- coding: utf-8 -*-
import os
import tempfile


def pytest_configure(config):
    if not config.option.basetemp:
        config.option.basetemp = os.path.join(tempfile.gettempdir(), 'DemoBrief-pytest')
