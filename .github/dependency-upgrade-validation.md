# Dependency upgrade validation

The AOTools workflow is the first caller supporting validation without Anaconda
publication. This change does not upgrade AOtools or change Build All.

## Run the pilot

1. Open Actions → AOTools → Run workflow.
2. Select the reviewed validation branch (or an approved AOtools upgrade branch).
3. Leave `publish` disabled, `dev` false, and enter a numeric build number.
4. Use `main` as the dependency label unless a reviewed test label is needed.
5. Confirm both the build and local Python 3.12 install/import test succeed.
6. Attach the run link and tested commit to the draft PR.

Conda builds use `--no-anaconda-upload` and run recipe tests normally. Built
packages are retained as GitHub artifacts for seven days. The install-test job
downloads those packages, indexes a local channel, and qualifies the requested
package against that channel so it cannot silently test a remote build. Imports
run outside the checkout. Build and install-test logs are also retained.

AOtools publishing requires explicitly enabling `publish`; only then is its
Anaconda secret passed to Build Base. Other existing callers retain publishing
behavior through Build Base's `publish: true` default. Build Base always disables
Conda's automatic upload; the explicit Anaconda upload step remains the publishing
path. Artifact storage in GitHub is distinct from publishing packages to Anaconda.

## Current scope

- AOtools validation: linux-64, Python 3.12, existing recipe variants.
- Build Base supports matching native artifacts per runner and sharing noarch
  artifacts across its existing test platforms.
- Other package workflows do not yet expose the new input.
- Coupled upgrades still need artifact transfer between builds; disabling upload
  alone does not make Build All test newly built prerequisites.
- Automatic PR triggers and GPU runtime integration tests are later steps.
- No actual Conda build has been run during local verification of this change.

After the pilot passes, extend the same mode to an ARM-enabled package, then
coupled groups. Use the existing Maintainer agent for approved version changes.

## AOtools automatic publication after merge

`validate_aotools_pr.yml` reads `aotools/release.json` for the build number.
The version remains in Build Base. Set build_number to 0 for a new version;
increment it for a rebuild of the same version. Include that change in the PR.

On relevant pushes to main, `publish_aotools_main.yml` runs scanner tests and
checks the public channel for the version/build and Python variants in the recipe.
A complete publication is skipped (including existing 1.0.8 build 0); partial
publication or network errors stop the workflow for investigation. For a missing
release, a publishing token is required, local artifact validation runs first,
then the existing workflow rebuilds, uploads and tests from the main channel.
PR jobs receive no publishing secret. Only the publication call receives it.
No force/overwrite flag is used. Do not run manual publication concurrently.

This workflow is triggered by pushes to main, including direct pushes. Protect
main through the team's normal PR review rules; merging is the publication decision.
Documentation-only and validation-only edits do not trigger publication. Shared
build workflow changes trigger a check but do not republish an existing identity.
For recipe changes that need distribution, increment the build number explicitly.
The workflow does not promote the exact PR artifact: it rebuilds the merged code.
Automatic end-to-end publication of a new identity still requires CI verification.
