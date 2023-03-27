#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    OSKAR_VERSION=$(echo "${OSKAR_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${OSKAR_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${OSKAR_VERSION}/" meta.yaml
fi

cd ./python
export OSKAR_LIB_DIR=$CONDA_PREFIX/lib
export OSKAR_INC_DIR=$CONDA_PREFIX/include
$PYTHON -m pip install --no-deps .
