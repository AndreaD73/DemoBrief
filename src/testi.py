# -*- coding: utf-8 -*-
"""Funzioni testuali condivise dal costruttore e dalla fusione."""
import difflib
import re

def let(s):
    return re.sub(r'[^a-z]', '', s.lower())

def dig(s):
    return re.sub(r'\D', '', s)

def ratio(a, b):
    return difflib.SequenceMatcher(None, let(a), let(b)).ratio()

def stessi(a, b, soglia=0.8):
    return dig(a) == dig(b) and ratio(a, b) >= soglia

def pulisci_ocr(s):
    s = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', s)
    s = re.sub(r'[^\x20-\x7e]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip(' .,;:|')
    return s

def utile(s):
    a = len(re.findall(r'[A-Za-z]', s))
    return a >= 3 and (a + len(re.findall(r'\d', s))) >= 0.6 * len(s.replace(' ', ''))

def lev(a, b):
    if abs(len(a) - len(b)) > 2:
        return 9
    p = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        c = [i]
        for j, y in enumerate(b, 1):
            c.append(min(p[j] + 1, c[j - 1] + 1, p[j - 1] + (x != y)))
        p = c
    return p[-1]
