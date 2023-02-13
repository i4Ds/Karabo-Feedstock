#!/bin/sh

# install
export CMAKE_ARGS="-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCUDA_ARCH=8.0" 

echo $PYTHON
$PYTHON --version
$PYTHON -m pip --version

$PYTHON -m pip install . --no-deps
