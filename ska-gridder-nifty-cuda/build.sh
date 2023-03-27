#!/bin/sh

git checkout --track origin/sim-874-python-wrapper
cd python
$PYTHON -m pip install --no-deps . 
