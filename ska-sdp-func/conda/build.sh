#!/bin/sh

# install
export CMAKE_ARGS="-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCUDA_ARCH=8.0" 
$PYTHON -m pip install . --no-deps
