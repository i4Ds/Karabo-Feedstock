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

After merging, use **Actions → Weekly Feedstock Dependency Scan → Run
workflow → test-notification** to verify issue assignment and notification.
Close that test issue, then run `scan` to test the real tracking issue.
