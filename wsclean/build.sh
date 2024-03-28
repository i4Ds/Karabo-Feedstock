#!/bin/bash

mkdir -p build
cd build
cmake -DCMAKE_INSTALL_PREFIX=$PREFIX ..
make -j 4
make install
