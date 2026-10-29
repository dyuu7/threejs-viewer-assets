"""Promote verified commits and resume immutable PyPI/GitHub releases."""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from project import ROOT, VERSION_LINE, ValidationError, digest, require, validate_versions
from validate import validate_distributions

REPOSITORY = "dyuu7/threejs-assets"


def command(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


def git(*args):
    return command("git", *args).decode().strip()


def api(endpoint, *, method="GET", data=None, missing=False, binary=False):
    args = ["gh", "api", f"repos/{REPOSITORY}/{endpoint}", "--method", method]
    if binary:
        args += ["--header", "Accept: application/octet-stream"]
    if data is not None:
        args += ["--input", "-"]
    try:
        result = command(*args, input=json.dumps(data).encode() if data is not None else None)
    except subprocess.CalledProcessError as exc:
        if missing and b"(HTTP 404)" in exc.stderr:
            return None
        raise ValidationError(exc.stderr.decode().strip()) from exc
    return result if binary else (json.loads(result) if result else None)


def version_at(revision):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Release revision must be an exact commit SHA")
    require(
        git("rev-parse", f"{revision}^{{commit}}") == revision,
        "Expected a commit, not a tag object",
    )
    source = git("show", f"{revision}:src/threejs_assets/__init__.py")
    match = VERSION_LINE.search(source)
    require(match is not None, "Missing release version")
    version = match.group(1)
    manifest = json.loads(git("show", f"{revision}:src/threejs_assets/assets/manifest.json"))
    validate_versions(version, manifest["upstream"]["version"])
    return version


def tag_commit(tag):
    reference = api(f"git/ref/tags/{tag}", missing=True)
    if reference is None:
        return None
    target = reference["object"]
    for _ in range(8):
        if target["type"] == "commit":
            return target["sha"]
        require(target["type"] == "tag", f"Unexpected target of {tag}")
        target = api(f"git/tags/{target['sha']}")["object"]
    raise ValidationError(f"Too many nested tags: {tag}")


def ancestor(revision, main):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", revision, main])
    require(result.returncode in (0, 1), "Cannot determine release ancestry")
    return result.returncode == 0


def check_candidate(revision, main):
    require(ancestor(main, revision), "main advanced; regenerate the update")
    paths = git("diff", "--name-only", main, revision).splitlines()
    require(paths, "Upstream update is empty")
    require(
        all(
            path in {"uv.lock", "src/threejs_assets/__init__.py"}
            or path.startswith("src/threejs_assets/assets/")
            for path in paths
        ),
        "Automatic promotion may change only resources, their version, and uv.lock",
    )


def pypi_files(version):
    url = f"https://pypi.org/pypi/threejs-assets/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            files = json.load(response)["urls"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    result = {}
    for item in files:
        require(not item["yanked"], f"PyPI file was yanked: {item['filename']}")
        require(item["filename"] not in result, "Duplicate PyPI filename")
        result[item["filename"]] = item["digests"]["sha256"]
    return result


def filenames(version):
    return {f"threejs_assets-{version}-py3-none-any.whl", f"threejs_assets-{version}.tar.gz"}


def asset_hash(asset):
    checksum = asset.get("digest") or ""
    if checksum.startswith("sha256:"):
        return checksum.removeprefix("sha256:")
    return digest(api(f"releases/assets/{asset['id']}", binary=True))


def completed(version):
    release = api(f"releases/tags/v{version}", missing=True)
    if release is None or release["draft"] or release["prerelease"]:
        return False
    remote = pypi_files(version)
    assets = release["assets"]
    return (
        set(remote) == filenames(version)
        and len(assets) == len(remote)
        and {asset["name"] for asset in assets} == set(remote)
        and all(
            asset["state"] == "uploaded" and asset_hash(asset) == remote[asset["name"]]
            for asset in assets
        )
    )


def prepare(event_ref):
    revision = git("rev-parse", "HEAD")
    if event_ref != "refs/heads/main":
        return {"revision": revision, "sync": False}
    current = plan(revision, False, event_ref)
    if current["needed"]:
        summary("Finish the current release first; the next run will check for upstream updates.")
        return {"revision": current["revision"], "sync": False}
    return {"revision": revision, "sync": True}


def plan(revision, promote, event_ref):
    version = version_at(revision)
    tag = f"v{version}"
    if event_ref.startswith("refs/tags/"):
        require(event_ref == f"refs/tags/{tag}", "Release tag does not match package version")
    pinned = tag_commit(tag)
    main = git("rev-parse", "origin/main")
    if pinned:
        require(not promote or pinned == revision, "An upstream update reuses a release version")
        require(ancestor(pinned, main), "Release tag must refer to a commit merged into main")
        require(version_at(pinned) == version, "Existing tag has a different package version")
        if event_ref.startswith("refs/tags/"):
            require(pinned == revision, "Triggered release tag changed")
        revision = pinned
    elif promote and not ancestor(revision, main):
        check_candidate(revision, main)
    else:
        require(ancestor(revision, main), "Release commit must already be merged into main")
    return {"revision": revision, "needed": not (pinned and completed(version))}


def accept(revision, promote):
    version = version_at(revision)
    git("fetch", "origin", "main")
    main = git("rev-parse", "origin/main")
    if not ancestor(revision, main):
        require(promote, "Unmerged release commit")
        check_candidate(revision, main)
        # A non-forced ref update is atomic: concurrent changes to main reject the
        # fast-forward instead of merging untested content into the candidate.
        api("git/refs/heads/main", method="PATCH", data={"sha": revision, "force": False})
    tag = f"v{version}"
    pinned = tag_commit(tag)
    if pinned is None:
        api("git/refs", method="POST", data={"ref": f"refs/tags/{tag}", "sha": revision})
    else:
        require(pinned == revision, f"Refusing to move existing tag {tag}")


def distributions(directory):
    wheel, sdist = validate_distributions(directory)
    return {path.name: digest(path.read_bytes()) for path in (wheel, sdist)}


def missing_files(expected, remote):
    require(set(remote) <= set(expected), "Unexpected files already exist in this PyPI version")
    for name, checksum in remote.items():
        require(checksum == expected[name], f"Published bytes differ: {name}; use a new .postN")
    return set(expected) - set(remote)


def stage(directory, pending):
    version = version_at(git("rev-parse", "HEAD"))
    expected = distributions(directory)
    missing = missing_files(expected, pypi_files(version))
    pending.mkdir()  # Never include stale files from another attempt.
    for name in sorted(missing):
        shutil.copyfile(directory / name, pending / name)
    return {"pending": bool(missing)}


def verify(directory):
    version = version_at(git("rev-parse", "HEAD"))
    expected = distributions(directory)
    for attempt in range(6):
        missing = missing_files(expected, pypi_files(version))
        if not missing:
            return version, expected
        if attempt < 5:
            time.sleep(5)
    raise ValidationError(f"PyPI publication incomplete: {', '.join(sorted(missing))}")


def github_release(directory):
    version, expected = verify(directory)
    tag = f"v{version}"
    require(tag_commit(tag) == git("rev-parse", "HEAD"), "Release tag no longer matches artifacts")
    release = api(f"releases/tags/{tag}", missing=True)
    if release is None:
        upstream = json.loads((ROOT / "src/threejs_assets/assets/manifest.json").read_text())[
            "upstream"
        ]["version"]
        release = api(
            "releases",
            method="POST",
            data={
                "tag_name": tag,
                "name": tag,
                "draft": True,
                "body": (
                    f"Official Three.js {upstream} ES modules and selected addons.\n\n"
                    f"[PyPI](https://pypi.org/project/threejs-assets/{version}/) · "
                    f"[Upstream](https://github.com/mrdoob/three.js/releases/tag/r{upstream.split('.')[1]})"
                ),
            },
        )
    require(not release["prerelease"], "Unexpected prerelease")
    existing = {asset["name"]: asset for asset in release["assets"]}
    require(set(existing) <= set(expected), "Unexpected GitHub release assets")
    for name, checksum in expected.items():
        asset = existing.get(name)
        if asset and asset["state"] != "uploaded":
            api(f"releases/assets/{asset['id']}", method="DELETE")
            asset = None
        if asset:
            require(asset_hash(asset) == checksum, f"GitHub release bytes differ: {name}")
        else:
            command("gh", "release", "upload", tag, str(directory / name), "--repo", REPOSITORY)
    # Keep a new release in draft until every artifact has been uploaded and verified.
    assets = api(f"releases/{release['id']}")["assets"]
    require(
        len(assets) == len(expected)
        and {asset["name"]: asset_hash(asset) for asset in assets} == expected,
        "GitHub release artifact verification failed",
    )
    if release["draft"]:
        api(f"releases/{release['id']}", method="PATCH", data={"draft": False})
    summary(f"Published https://github.com/{REPOSITORY}/releases/tag/{tag}")


def summary(message):
    print(message)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8") as output:
            output.write(message + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["prepare", "plan", "accept", "stage", "verify", "github"]
    )
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        output = {}
        if args.operation == "prepare":
            output = prepare(os.environ["RELEASE_EVENT_REF"])
        elif args.operation in {"plan", "accept"}:
            revision = os.environ["RELEASE_REVISION"]
            promote = os.environ.get("RELEASE_PROMOTE") == "true"
            if args.operation == "plan":
                output = plan(revision, promote, os.environ.get("RELEASE_EVENT_REF", ""))
                if not output["needed"]:
                    summary("This version is already fully published; no rebuild or upload needed.")
            else:
                accept(revision, promote)
        elif args.operation == "stage":
            output = stage(ROOT / "dist", ROOT / "pending")
        elif args.operation == "verify":
            verify(ROOT / "dist")
        else:
            github_release(ROOT / "dist")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as destination:
                for name, value in output.items():
                    destination.write(
                        f"{name}={str(value).lower() if isinstance(value, bool) else value}\n"
                    )
    except (ValidationError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release failed: {exc}\n")


if __name__ == "__main__":
    main()
