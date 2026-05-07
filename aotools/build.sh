#!/bin/sh
# SafeConfigParser was removed in Python 3.12 — replace with RawConfigParser
sed -i 's/configparser\.SafeConfigParser/configparser.RawConfigParser/g' versioneer.py

$PYTHON -m pip install --no-deps .
