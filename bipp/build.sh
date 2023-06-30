mkdir build
cd build
cmake .. -DBIPP_PYTHON=ON -DBIPP_INSTALL=PYTHON -DBIPP_PYBIND11_DOWNLOAD=ON
make install
