#!/bin/bash

mkdir build && cd build
cmake -DCMAKE_INSTALL_PREFIX=$PREFIX ..
# cmake -DCMAKE_INSTALL_PREFIX=$PREFIX -DCMAKE_CXX_FLAGS="-fpermissive" ..
make install
