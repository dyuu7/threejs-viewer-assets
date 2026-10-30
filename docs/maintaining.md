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
Editor tools, physics, and game controls are outside this scope.

`scripts/project.py` defines the supported `ENTRYPOINTS`; keep the README's
scope in sync with that list. The manifest records these entries separately
from the complete module dependency closure. Files included only as dependencies
may change with upstream and carry no independent entry point guarantee.

Packaging-only `.postN` revisions preserve the supported entries and upstream
resource bytes. For a scope change, update the selection, documentation, and
browser coverage together in a reviewed upstream-version release. Document
entry point removals, replacements, and required application changes in its
release notes. Review the upstream migration guide and release notes before
adopting a new Three.js version; the example passing does not establish downstream
application compatibility.

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

## Rename and first publication

The old `threejs-assets` 0.186.1 release and `v0.186.1` tag belong to the previous
package identity. Preserve them. The first `threejs-viewer-assets` release is
0.186.1.post1, using `v0.186.1.post1`, so release recovery cannot select the old
tag or reuse its artifacts. The upstream manifest remains at Three.js 0.186.1.

When applying the rename:

1. Rename the GitHub repository to `dyuu7/threejs-viewer-assets` and update the
   checkout's origin to `git@github.com:dyuu7/threejs-viewer-assets.git`.
2. In the owning PyPI account, register a **new pending Trusted Publisher** using
   the exact fields above. The publisher for the old PyPI project does not grant
   permission to create the new one. `gh` can manage GitHub but cannot configure
   the PyPI account. A missing public project page does not reserve the name;
   PyPI checks name eligibility when registering or publishing.
3. Commit and push the renamed package and workflow to `main`, and wait for CI.
   The release workflow's repository guard requires the new GitHub name.
4. Run `gh workflow run publish.yml --repo dyuu7/threejs-viewer-assets --ref main`
   after the publisher is configured. The first upload creates the PyPI project;
   verify both distributions and the GitHub release for `v0.186.1.post1`.

Consumers must replace both the distribution dependency and Python resource
package name as described in the README. Keep the old PyPI files available for
existing pins; the projects do not redirect or upgrade across names. Subsequent
releases target only `threejs-viewer-assets`.

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
