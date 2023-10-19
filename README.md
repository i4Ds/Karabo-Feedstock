# Karabo-Feedstock<a id="feedstock"></a>

This repository builds the conda-dependencies for the [Karabo pipeline](https://github.com/i4Ds/Karabo-Pipeline) so that conda-wheel of the pipeline can be built. To do this, the individual packages must be triggered by `build * dependenc(y/ies) *` workflows, which orchestrate other workflows located in this repository. There are two different kind of builds to consider: *Main builds* & *Dev builds*.

**Note**: The builds of the feedstock are from 3th party repositories or forked repositories to make them compatible with the Karabo pipeline. This means that the respective builds do not have correct dpendency resolvements because we cannot test this properly. So the main goal is to make sure that the builds related to a Karabo installation have a correct dependency resolvement. If the constraints of a build are too tight or too loose, we can correct this. We ask to open an issue in such a case.

**Dev Info:** The conda build-string (to distinguish whether to overwrite an existing wheel or not) is defined in each `meta.yaml` and usually dependent on the version string and python version. So changing the label only doesn't change the bulid string and that could overwrite an existring conda wheel. To be sure, please have a look into the according `meta.yaml` before creating a new build with an already existing package-version.

## Main builds
Main builds are Conda wheels which are needed for a Karabo build. These packages should have the `main` label, so that they can be installed without additional referencing of another channel. If the release number is greater than previous releases, the package automatically becomes `latest`.

## Dev builds
Dev builds are Conda wheels, which are used for development purpose, but should not appear under `latest`. This ensures that a Karabo installation will not be broken because of a newer version of a dependency. The naming convention is [PEP 440](https://peps.python.org/pep-0440/) compliant: `{MAJOR}.{MINOR}.dev{PATCH}`, which is automatically formed this way if the `dev` flag is set when starting a workflow. The current setup calls for the builds to have the `main` label as well. This must be compatible with the [environment.yaml](https://github.com/i4Ds/Karabo-Pipeline/blob/main/environment.yaml) of the Karabo pipeline, where they can easily be referenced.

Dev-builds can have other conda-dependencies which are also part of the Feedstock. In case you need a newer version of such a dependency, they should be a dev-build, to not create a `latest` wheel which could interfere with existing Karabo wheels. In case you want to refer to such dev-builds for building another dev-build, there is a `devDeps` option for each build which has such possible dependencies. If such an option is available, the names of each sub-dependency is comma separated (e.g. `{dep1}, {dep2}, {dep3}`) followed by a whitespace. The `dep` name must be a `build_base.yml env.{dep}` name where in the version string dev must NOT occur. The default value is to refer all sub-dependencies as dev-dependencies. *Note*: `devDeps` is only active if the `dev` flag is set, otherwise it has no effect.

**Creating new meta.yaml**: The setup relies on `packageVersionNames` in `build_base.yml`. To make the dev-setup work, you should use an `_ALT` suffix of the `packageVersionNames` in the `meta.yaml` in case you have a sub-dependency of this feedstock.

## Tree builds (deprecated, should get removed soon)
Tree builds usually have a `tree` keyword in the workflow. These are a special kind of builds, where a new build is dependent on other dependencies we build as well. The tree builds therefore build the whole dependency tree in a single super-workflow. In case a dev-tree-build is triggered setting the `dev` flag, each underlying sub-dependency is automatically considered to be a dev-build. An option allowing a mix between dev and non-dev builds of tree-builds is not implemented. Therefore, tree-builds shouldn't have a `devDeps` option.

## Version Management
**Build Version Convention**: Package and dependency versions are defined in `build_base.yml` and in the according `meta.yaml`. At best, keep build-versions consistent with the release-versions of the package-repository. There are some cases which require a specific commit which isn't a version-tag. In that case just keep the release prior to the according commit.

**Legacy Builds**: The feedstock evolves over time. This often results in incompatible build-recipes with legacy builds. If you intend to fix such a legacy-build (for whatever reason), checkout at the according commit, create a new branch with the fixes and then trigger the build which should overwrite the broken legacy wheel.

**Constrain Dependencies**: We intend to be nice to users of Karabo so they are able to install additional packages without breaking an environment. To enable that, we should be sure to not constrain well-known dependencies like `numpy`, `scipy` or `astropy` too much if possible. On the other hand, be sure to constrain packages with a lot of api-breaking changes and exclude versions of well-known packages with known issues (for Karabo). This approach relies on a lot of testing. But since we build a lot of conda-wheels of 3th party packages, it's too cumbersome to test them individually. Therefore, the aim should be to have a high test-coverage of Karabo.

## Feedstock Build Dependencies
In case you intend to create a new build which also needs underlying changes of other builds, it's useful to know all feedstock-dependencies to know which one(s) to build first. The following provides an overview of the dependencies. In the brackets are the transversal dependencies if they're not present as direct dependency  (please keep them updated):
aotools: 		        -
aratmospy: 		        -
bipp: 			        finufft(fftw3)
eidos: 			        -
fftw3: 			        -
finufft: 		        fftw3
hvox: 			        pycsou, rascil(pybdsf), ska-sdp-datamodels, ska-sdp-func-python
katbeam: 		        -
montagepy: 		        -
oskar: 			        -
oskar-py: 		        oskar
pfft: 			        fftw3
pinocchio: 		        pfft, fftw3
pybdsf: 		        -
pycsou: 		        -
rascil: 		        pybdsf, ska-sdp-datamodels, ska-sdp-func-python, ska-sdp-func
seqfile: 		        -
ska-gridder-nifty-cuda:	-
ska-sdp-datamodels: 	-
ska-sdp-func: 		    -
ska-sdp-func-python: 	ska-sdp-datamodels, ska-sdp-func
tools21cm: 		        fftw3