#!/bin/sh

cd ./python
export OSKAR_LIB_DIR=$CONDA_PREFIX/lib
export OSKAR_INC_DIR=$CONDA_PREFIX/include
$PYTHON -m pip install --no-deps .
