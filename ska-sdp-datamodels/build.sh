#!/bin/sh

# tar.gz has internal folder which contains the setup.py
cd $INTERNAL_FOLDER_NAME

# poetry seems to make troubles with conda - remove it
rm pyproject.toml

$PYTHON -m pip install --no-deps .
