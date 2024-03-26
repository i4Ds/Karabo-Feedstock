#!/bin/bash

./configure --prefix $PREFIX --enable-float
make
make install
