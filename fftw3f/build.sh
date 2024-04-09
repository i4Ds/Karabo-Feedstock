#!/bin/bash

if [ -z $mpi ]; then
  mpi='nompi'
fi

if [ $mpi = 'nompi' ]; then
  ./configure --prefix $PREFIX --enable-float
else
  ./configure --prefix $PREFIX --enable-float --enable-mpi
fi
make
make install
