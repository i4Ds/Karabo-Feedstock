# Feedstock dependency scanner

The weekly workflow reads `.github/dependency-scan.toml`, classifies every
version declared in `.github/workflows/build_base.yml`, and compares each active
automatic entry with a reviewed upstream release source. It runs every Monday
at 09:00 UTC and can also be started manually.

## What it does

1. Validates that every `*_VERSION` variable is registered exactly once.
2. Checks stable releases from PyPI, GitHub tags, or approved GitLab hosts.
3. Writes the complete result table to the GitHub Actions job summary.
4. Creates or updates one tracking issue when updates or scan errors exist.
5. Assigns and mentions the configured maintainer in that issue.
6. Adds a ready-to-copy task for the Karabo Feedstock Maintainer agent for each
   detected update.
7. Closes its open tracking issue after all automatically monitored packages
   are current again.

The scanner does not start the custom agent. A maintainer chooses one update,
starts the agent manually, and reviews its draft pull request.

## Python and NumPy compatibility

The registry contains one compatibility contract for the complete Feedstock:

- Python 3.12 is the required runtime;
- NumPy 2.2.6 is the required NumPy runtime where applicable;
- packages are built into an isolated local Conda directory;
- no validation package is uploaded to Anaconda.

The weekly release scan remains lightweight and read-only. Definitive
compatibility validation runs on a dependency-update pull request because a
release cannot be proven compatible from version metadata alone.

The custom agent first inspects upstream metadata, updates one dependency group,
runs the unit tests and compatibility preflight, and opens a draft pull request.
Opening that draft is what starts the authoritative build-and-test workflow.

`.github/workflows/dependency-compatibility.yml` selects dependency groups
whose version or recipe changed. For every selected group it:

1. verifies that the recipe variants include the configured Python and NumPy
   targets;
2. builds the registered recipe or coupled recipes without uploading;
3. creates a clean environment with the configured versions;
4. installs the exact locally built artifacts;
5. runs the registered Python smoke tests;
6. publishes the pass/fail table as the GitHub Actions job summary and retains
   the full report and log as an artifact for 14 days.

Compatibility modes are explicit:

- `python-numpy`: build and test with Python and NumPy plus Python smoke tests;
- `python-only`: build and test with Python where NumPy is not a dependency;
- `coinstall`: native package build and co-installation with Python and NumPy;
- `manual`: coordinated compatibility work that cannot be automated safely;
- `retired`: no release or compatibility work is performed.

CUDA recipes run serially and use the same CUDA/GCC setup as the existing
Feedstock build workflows. A compatibility check has only `contents: read`
permission and receives no repository or Anaconda write credential.

## Safety boundary

The workflow has only `contents: read` and `issues: write`. The scanner cannot
edit a recipe, dispatch a build, create or merge a pull request, create a tag,
or publish a package. Entries whose latest compatible version cannot be inferred
safely are marked `manual`; they remain visible without producing a false update.
Packages that are no longer built are marked `retired`; they remain registered
for coverage but are never queried and never produce an agent task.

The current retired entries are FFTW3F, RASCIL, HVOX, and Pycsou. FFTW itself
remains active because `finufft/meta.yaml` still pins `FFTW3_VERSION`.

## Registry structure

Each `[[dependencies]]` entry defines:

- the version variable or coupled variables;
- the relevant recipe files;
- one trusted release provider and release page;
- an optional tag pattern, compatibility note, or ignored stable version.
- a compatibility mode, validation recipes/package names, optional smoke tests,
  and whether the existing CUDA setup is required.

Supported providers are `pypi`, `github-tags`, `gitlab-tags`, `manual`, and
`retired`.
GitLab access is allowlisted to `gitlab.com` and `git.astron.nl`.

Use `ignored_versions` only after a maintainer has documented why a real stable
release is incompatible, for example:

```toml
ignored_versions = ["2.0.0"]
notes = "2.0.0 is incompatible with the supported Python matrix; see issue #123."
```

When adding a new version to `build_base.yml`, add its registry entry in the
same pull request. The coverage test intentionally fails otherwise.

## Local validation

Run the offline unit and coverage tests:

```bash
python -m unittest discover -s .github/scripts/tests -p 'test_*.py' -v
```

Run a real read-only release comparison without creating an issue:

```bash
python .github/scripts/dependency_scan.py \
  --repo-root . \
  --config .github/dependency-scan.toml \
  --mode scan \
  --dry-run
```

Validate the compatibility configuration for one dependency without building:

```bash
python .github/scripts/dependency_compatibility.py \
  --repo-root . \
  --config .github/dependency-scan.toml \
  validate \
  --dependency aotools \
  --preflight-only \
  --report /tmp/aotools-compatibility.md
```

After the compatibility workflow is merged, it runs automatically on relevant
pull requests. It can also be tested manually from **Actions → Dependency
Python and NumPy Compatibility → Run workflow** by entering a registry id such
as `aotools` and selecting the dependency-update branch.

After merging, use **Actions → Weekly Feedstock Dependency Scan → Run
workflow → test-notification** to verify issue assignment and notification.
Close that test issue, then run `scan` to test the real tracking issue.
