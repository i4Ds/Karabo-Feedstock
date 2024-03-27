#!/bin/bash

mkdir build; cd build

if [ -z $mpi ]; then
  mpi='nompi'
fi

if [ $mpi = 'nompi' ]; then
  BUILD_WITH_MPI="OFF"
else
  BUILD_WITH_MPI="ON"
fi

cmake -DCMAKE_INSTALL_PREFIX=$PREFIX -DBUILD_LIB_CUDA="ON" -DBUILD_WITH_MPI=$BUILD_WITH_MPI ..

make install
