#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    SKA_SDP_FUNC_VERSION=$(echo "${SKA_SDP_FUNC_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${SKA_SDP_FUNC_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${SKA_SDP_FUNC_VERSION}/" meta.yaml
fi

export SKA_SDP_FUNC_LIB_DIR=$CONDA_PREFIX/lib
export CMAKE_ARGS="-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX" 
$PYTHON -m pip install --no-deps . 
