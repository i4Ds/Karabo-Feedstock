#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    PYBDSF_VERSION=$(echo "${PYBDSF_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${PYBDSF_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${PYBDSF_VERSION}/" meta.yaml
fi

$PYTHON -m pip install --no-deps . 
