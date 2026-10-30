# Maintainer guide

## Verify a change

Use Python 3.12+, Node.js 24, and uv. Node.js is a maintenance tool; consumers
need Python and a browser supporting ES modules, import maps, and WebGL2.

```sh
npm ci --ignore-scripts
uv sync --locked --group browser
uv run --locked --group browser playwright install --with-deps chromium
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -m "not browser"
uv build
uv run --locked twine check --strict dist/*
uv run --locked --group browser python scripts/check_dist.py --browser
```

The last command verifies both distributions, rebuilds offline, installs the
wheel in a fresh environment, and runs the browser test against that installation.
A fully offline build requires the Python build dependencies to be installed first.

## Viewer resource policy

Select addons for the viewing tasks documented in the README: camera navigation,
geometry and model display, labels, and outline highlighting. A proposed entry
must address one of these tasks, identify any additional offline resources or
decoders, and have an installed-wheel browser test that exercises its behavior.

`scripts/project.py` defines the supported `ENTRYPOINTS`; keep the README's
scope in sync with that list. The manifest records these entries separately
from the complete module dependency closure.

Update the selection, documentation, and browser coverage together when changing
the scope. Review upstream release notes and migration guidance when adopting a
new Three.js version, and describe relevant API changes in the release notes.

## Update resources

```sh
uv run python scripts/update_threejs.py                  # npm latest
uv run python scripts/update_threejs.py --version 0.186.1 # exact release
uv lock
```

Run verification and review the upstream changes before committing. For a
packaging-only fix, bump `__version__` to `.postN` and run `uv lock`; keep the
upstream manifest unchanged. Use a clean `dist/` when changing release versions.

## Enable unattended releases

The daily **Release** workflow checks npm and opens an update PR. An unfinished
release takes priority over the next upstream update. Full CI verifies the exact
candidate commit before main is fast-forwarded.
Publication uses OIDC; no PyPI API token or GitHub PAT is needed.

One-time configuration:

- Allow Actions to create pull requests in **Settings → Actions → General**.
- Create the **`pypi` environment**, allow `main` and `v*` tags, and leave required
  reviewers disabled.
- Configure a [pending Trusted Publisher](https://pypi.org/manage/account/publishing/)
  (or a publisher on an existing project):

| Field | Value |
| --- | --- |
| Project | `threejs-viewer-assets` |
| GitHub owner | `dyuu7` |
| Repository | `threejs-viewer-assets` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

Save the publisher, then run **Actions → Release → Run workflow** on `main`,
leaving the version empty. The first successful upload creates the PyPI project;
subsequent releases use the same publisher. No repository secrets or variables
are needed. Keep `v*` tags immutable; main must allow verified fast-forward updates.

## Recovery and packaging fixes

Failed checks stop promotion and publication. GitHub Actions reports failures;
subscribe to workflow notifications to receive alerts. Rerun the failed workflow,
or let the next daily run retry. Existing PyPI and GitHub files must match the
verified artifacts byte for byte; only missing files are uploaded. A completed
release is a no-op. GitHub Releases become public only after PyPI is complete.

For packaging fixes, merge a new `.postN` version into main and run **Release**
(or wait for the daily run). To retry an older version, run **Release** on its tag.
Never move a release tag or replace published files. A hash mismatch needs
investigation and, if the package must change, a new version.
