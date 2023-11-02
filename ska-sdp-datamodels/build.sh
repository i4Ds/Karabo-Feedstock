#!/bin/sh

# tar.gz has internal folder which contains the setup.py
cd $INTERNAL_FOLDER_NAME

$PYTHON -m pip install --no-deps .
