#!/bin/bash

# Force CMake to use the Conda Python interpreter.
# Without this, GitHub Actions often selects the system Python (3.12),
# which does NOT ship with development headers (pyconfig.h), causing
# radler and pybind11 to fail during configuration.
#
# Setting these ensures consistent, reliable builds across local, CI,
# and all conda-build environments.
mkdir -p build
cd build
cmake -DCMAKE_INSTALL_PREFIX=$PREFIX .. \
    -DCMAKE_CXX_STANDARD=17 -DCMAKE_CXX_STANDARD_REQUIRED=ON \
    -DPYTHON_EXECUTABLE="${PYTHON}" -DPython3_EXECUTABLE="${PYTHON}" \ 
make -j 4
make install