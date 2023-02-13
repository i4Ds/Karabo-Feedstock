#!/bin/sh

# install
CMAKE_ARGS="-DCUDA_ARCH=8.0" 
$PYTHON -m pip install . --no-deps

# test
$PYTHON -m pip install pytest
pytest
