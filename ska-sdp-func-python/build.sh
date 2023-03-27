#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    SKA_SDP_FUNC_PY_VERSION=$(echo "${SKA_SDP_FUNC_PY_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${SKA_SDP_FUNC_PY_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${SKA_SDP_FUNC_PY_VERSION}/" meta.yaml
fi

# tar.gz has internal folder which contains the setup.py
cd $INTERNAL_FOLDER_NAME

$PYTHON -m pip install --no-deps .
