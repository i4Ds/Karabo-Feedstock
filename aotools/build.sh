#!/bin/sh
set -eu

if [ -f versioneer.py ]; then
    sed -i 's/configparser\.SafeConfigParser/configparser.RawConfigParser/g' versioneer.py
    sed -i 's/parser\.readfp(/parser.read_file(/g' versioneer.py
fi

"$PYTHON" -m pip install --no-deps .
