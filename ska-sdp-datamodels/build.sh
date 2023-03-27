#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    SKA_SDP_DTMDL_VERSION=$(echo "${SKA_SDP_DTMDL_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${SKA_SDP_DTMDL_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${SKA_SDP_DTMDL_VERSION}/" meta.yaml
fi

# tar.gz has internal folder which contains the setup.py
cd $INTERNAL_FOLDER_NAME

# poetry seems to make troubles with conda - remove it
rm pyproject.toml

$PYTHON -m pip install --no-deps .
