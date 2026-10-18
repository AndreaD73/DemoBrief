# -*- coding: utf-8 -*-
"""Text helpers shared by the story builder and the merge step.

Pure functions on strings (OCR clean-up, fuzzy comparison, edit distance);
they read and write nothing.
"""
import difflib
import re

# Minimum share of letters+digits over non-space characters for an OCR line to be useful.
MIN_ALNUM_SHARE = 0.6
# Minimum number of letters for an OCR line to be useful.
MIN_LETTERS = 3
# Distance returned by lev() when the length gap already rules out a near match.
FAR_DISTANCE = 9
# Largest length gap for which lev() computes the real distance.
MAX_LENGTH_GAP = 2


def let(text):
    """Return the lowercase letters a-z of `text`, dropping everything else."""
    return re.sub(r'[^a-z]', '', text.lower())


def dig(text):
    """Return only the decimal digits of `text`."""
    return re.sub(r'\D', '', text)


def ratio(a, b):
    """Similarity (0..1) between the letters of `a` and `b`, ignoring digits and punctuation."""
    return difflib.SequenceMatcher(None, let(a), let(b)).ratio()


def stessi(a, b, soglia=0.8):
    """True when two OCR strings carry the same digits and similar letters (ratio >= `soglia`)."""
    return dig(a) == dig(b) and ratio(a, b) >= soglia


def pulisci_ocr(s):
    """Normalise a raw OCR line: split glued words, drop non-ASCII, squeeze blanks, trim edges."""
    s = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', s)
    s = re.sub(r'[^\x20-\x7e]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip(' .,;:|')
    return s


def utile(s):
    """True when an OCR line has enough letters and is mostly alphanumeric (not noise)."""
    letters = len(re.findall(r'[A-Za-z]', s))
    digits = len(re.findall(r'\d', s))
    return letters >= MIN_LETTERS and (letters + digits) >= MIN_ALNUM_SHARE * len(s.replace(' ', ''))


def lev(a, b):
    """Levenshtein distance between `a` and `b`, or FAR_DISTANCE if their lengths differ by > 2."""
    if abs(len(a) - len(b)) > MAX_LENGTH_GAP:
        return FAR_DISTANCE
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]
