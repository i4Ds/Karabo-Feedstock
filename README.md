# Karabo-Feedstock<a id="feedstock"></a>

This repository builds the conda-dependencies for the [Karabo pipeline](https://github.com/i4Ds/Karabo-Pipeline) so that conda-wheel of the pipeline can be built. To do this, the individual packages must be triggered by `build * dependenc(y/ies) *` workflows, which orchestrate other workflows located in this repository. There are two different kind of builds to consider: *Main builds* & *Dev builds*.

**Note**: The builds of the feedstock are from 3th party repositories or forked repositories to make them compatible with the Karabo pipeline. This means that the respective builds do not have correct dpendency resolvements because we cannot test this properly. So the main goal is to make sure that the builds related to a Karabo installation have a correct dependency resolvement. If the constraints of a build are too tight or too loose, we can correct this. We ask to open an issue in such a case.

## Main builds
Main builds are Conda wheels which are needed for a Karabo build. These packages should have the `main` label, so that they can be installed without additional referencing of another channel. If the release number is greater than previous releases, the package automatically becomes `latest`.

## Dev builds
Dev builds are Conda wheels, which are used for development purpose, but should not appear under `latest`. This ensures that a Karabo installation will not be broken because of a newer version of a dependency. The naming convention is [PEP 440](https://peps.python.org/pep-0440/) compliant: `{MAJOR}.{MINOR}.dev{PATCH}`, which is automatically formed this way if the `dev` flag is set when starting a workflow. The current setup calls for the builds to have the `main` label as well. This must be compatible with the [environment.yaml](https://github.com/i4Ds/Karabo-Pipeline/blob/main/environment.yaml) of the Karabo pipeline, where they can easily be referenced.

Dev-builds can have other conda-dependencies which are also part of the Feedstock. In case you need a newer version of such a dependency, they should be a dev-build, to not create a `latest` wheel which could interfere with existing Karabo wheels. In case you want to refer to such dev-builds for building another dev-build, there is a `devDeps` option for each build which has such possible dependencies. If such an option is available, the names of each sub-dependency is comma separated (e.g. `{dep1}, {dep2}, {dep3}`) followed by a whitespace. The `dep` name must be a `build_base.yml env.{dep}` name where in the version string dev must NOT occur. The default value is to refer all sub-dependencies as dev-dependencies. *Note*: `devDeps` is only active if the `dev` flag is set, otherwise it has no effect.

**Creating new meta.yaml**: The setup relies on `packageVersionNames` in `build_base.yml`. To make the dev-setup work, you should use an `_ALT` suffix of the `packageVersionNames` in the `meta.yaml` in case you have a sub-dependency of this feedstock.

## Version Management
**Build Version Convention**: Package and dependency versions are defined in `build_base.yml` and in the according `meta.yaml`. At best, keep build-versions consistent with the release-versions of the package-repository. There are some cases which require a specific commit which isn't a version-tag. In that case just keep the release prior to the according commit.

**Legacy Builds**: The feedstock evolves over time. This often results in incompatible build-recipes with legacy builds. If you intend to fix such a legacy-build (for whatever reason), checkout at the according commit, create a new branch with the fixes and then trigger the build which should overwrite the broken legacy wheel.

**Constrain Dependencies**: We intend to be nice to users of Karabo so they are able to install additional packages without breaking an environment. To enable that, we should be sure to not constrain well-known dependencies like `numpy`, `scipy` or `astropy` too much if possible. On the other hand, be sure to constrain packages with a lot of api-breaking changes and exclude versions of well-known packages with known issues (for Karabo). This approach relies on a lot of testing. But since we build a lot of conda-wheels of 3th party packages, it's too cumbersome to test them individually. Therefore, the aim should be to have a high test-coverage of Karabo.

**About Build-String and Versions**: The uniqueness of a conda-wheel is determined by the uniqueness of the build-string and NOTHING ELSE. So if another wheel has the exact same build-string, even if it's build completely different, it would overwrite an already existing conda-wheel on [anaconda.org](https://anaconda.org/). This can brake package installations which depend on the accidentally removed package. So if you create a new `meta.yaml`, pay special attention to the `build` section to prevent issues. When creating a new conda build, please increase the `build-number` for each new build of the same package version, so conda knows which version to install. MPI-based build-numbers have an offset of +100 to prefer nompi builds if not explicitly specified.

## About MPI
**General**: Currently, there are three different options on how a conda-wheel can be build with mpi. It's either "nompi", "openmpi" or "mpich". It seems like other packages like [h5py](https://anaconda.org/conda-forge/h5py/files) include this in their build-string and so should we. This makes it clear to others which packages use mpi and which mpi implementation they were built with by using `conda list | grep mpi`. The setup of the github workflow and the conda-recipe should be build accordingly so that once you build a wheel, you just have to care about setting `MPI` and `MPI_VERSION` in `build_base.yml` accordingly. So, we just have to care about that the according mpi-wheels of all Karabo-dependencies are avilable at [anaconda.org](https://anaconda.org/) (whether it's our channel or not) and that they're build with the same MPI (openmpi or mpich). Be aware that openmpi nor mpich is windows-compatible. But since we don't support windows you shouldn't have to worry too much.

**Installation of MPI-wheels**: Installation is fairly straight-forward. If there are mpi-wheels and no-mpi-wheels available, the no-mpi-wheels are chosen per default. If installing an mpi-build is needed (and let the dependencies figure out whether it is mpich or openmpi), just install it like the following: `conda install -c conda-forge <package>=*=mpi*`. If you want to be sure that it's mpich or openmpi, just install it with the very same installation instruction by appending `openmpi` or `mpich` at the end.

**Using Native MPI**: There are cases where using an MPI compiled on the target architecture is needed. We don't have to consider this use-case for the feedstock since there is a solution according to [conda-forge docs](https://conda-forge.org/docs/user/tipsandtricks.html). In this use-casee, the installer has to link the mpi of the environment to an MPI available at a standard-location as follows: `conda install "mpich=x.y.z=external_*"` or `conda install "openmpi=x.y.z=external_*"`.

## Feedstock Build Dependencies
**Active builds**: In case you intend to create a new build which also needs underlying changes of other builds, it's useful to know all feedstock-dependencies to know which one(s) to build first. The following provides an overview of the dependencies. In the brackets are the transversal dependencies if they're not already present as direct dependency. Please keep the list updated so that only karabo-latest builds are listed and that they have the correct direct and transversal dependencies:
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

**Removed builds**: To know on which commit to look for just in case.
