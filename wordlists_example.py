# stand-in for wordlists.py, which isn't public so nobody can read exactly what the filters catch.
# same names, a couple of patterns each
import re

NAMES = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German"}

# contact info
CONTACT = re.compile(r"\badd me on (?:ig|insta|telegram)\b|\bwhat'?s (?:your|ur) (?:ig|insta|number)\b", re.I)
OWN_CONTACT = re.compile(r"\b(?:ig|insta|telegram)\s*:\s*@?\w{3,}", re.I)
PHONE = re.compile(r"(?<!\w)\+?\d[\d \-().]{7,16}\d(?!\w)")
DATE = re.compile(r"\d{4}[-/.]\d\d?[-/.]\d\d?")
IP = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I)

# message filter
EN_SEVERE = re.compile(r"\b(?:wanna|let'?s) meet (?:up )?after school\b", re.I)
EN_INAPPROPRIATE = re.compile(r"\bdon'?t tell (?:your|ur) parents\b|\b(?:are|r) (?:you|u) home alone\b", re.I)

# spelling tricks
LOOKALIKES = str.maketrans({"а": "a", "е": "e", "о": "o"})  # cyrillic
INVISIBLE = re.compile("[​-‍﻿]")
LEET = str.maketrans({"0": "o", "1": "i", "3": "e"})
MIXED = re.compile(r"\b(?=\w*[a-z])(?=\w*[0-9])\w+", re.I)
DOTTED = re.compile(r"(?<![\w.])(?:[a-z]\.)+[a-z](?![\w])", re.I)
SPACED = re.compile(r"(?<![\w])(?:[a-z] ){2,}[a-z](?![\w])", re.I)
LONG_REPEATS = re.compile(r"([a-z])\1{2,}", re.I)
REPEATS = re.compile(r"([a-z])\1+", re.I)

LISTS = {}  # other languages: {"es": (severe, inappropriate)}
