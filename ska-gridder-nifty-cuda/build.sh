#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    SKA_GRDR_NFTY_CU_VERSION=$(echo "${SKA_GRDR_NFTY_CU_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${SKA_GRDR_NFTY_CU_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${SKA_GRDR_NFTY_CU_VERSION}/" meta.yaml
fi

git checkout --track origin/sim-874-python-wrapper
cd python
$PYTHON -m pip install --no-deps . 
