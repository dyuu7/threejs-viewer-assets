"""Synchronize the selected, unmodified ESM dependency closure from official npm."""

import argparse
import base64
import hashlib
import io
import json
import os
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from project import (
    ENTRYPOINTS,
    ROOT,
    VERSION_LINE,
    ValidationError,
    digest,
    json_bytes,
    module_closure,
    read_tree,
    read_version,
    require,
    source_url,
    validate_assets,
    version_tuple,
)

REGISTRY = "https://registry.npmjs.org"
MAX_DOWNLOAD = 64 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024


def official_url(url):
    parsed = urllib.parse.urlsplit(url)
    require(
        parsed.scheme == "https"
        and parsed.netloc == "registry.npmjs.org"
        and not parsed.query
        and not parsed.fragment,
        f"Nonofficial npm URL rejected: {url}",
    )


class RegistryRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    official_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "threejs-viewer-assets-maintenance/1"}
    )
    try:
        with urllib.request.build_opener(RegistryRedirects).open(request, timeout=45) as response:
            data = response.read(MAX_DOWNLOAD + 1)
        require(len(data) <= MAX_DOWNLOAD, "npm response exceeds size limit")
        return data
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValidationError(f"npm request failed for {url}: {exc}") from exc


def unpack_verified(data, integrity):
    require(isinstance(integrity, str) and integrity.startswith("sha512-"), "Missing SHA-512 SRI")
    try:
        expected = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    except ValueError as exc:
        raise ValidationError("Malformed npm integrity") from exc
    require(hashlib.sha512(data).digest() == expected, "npm tarball integrity mismatch")
    files = {}
    seen = set()
    total = 0
    # Never extract an upstream archive onto the filesystem or execute its scripts.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            require(
                path.parts
                and path.parts[0] == "package"
                and not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in member.name,
                f"Unsafe tar member: {member.name}",
            )
            require(member.name not in seen, f"Duplicate tar member: {member.name}")
            seen.add(member.name)
            require(len(seen) <= 10000, "Too many npm archive members")
            if member.isdir():
                continue
            require(member.isfile(), f"Nonregular tar member: {member.name}")
            total += member.size
            require(0 <= member.size <= MAX_UNPACKED and total <= MAX_UNPACKED, "Archive too large")
            name = str(path.relative_to("package"))
            require(name not in files, f"Duplicate normalized tar member: {name}")
            with archive.extractfile(member) as source:
                files[name] = source.read()
    return files


def prepare(version):
    version_tuple(version)
    metadata = json.loads(fetch(f"{REGISTRY}/three/{version}"))
    require(metadata.get("name") == "three", "Unexpected npm metadata package name")
    require(metadata.get("version") == version, "npm metadata version mismatch")
    distribution = metadata["dist"]
    require(distribution["tarball"] == source_url(version), "Unexpected npm tarball URL")
    data = fetch(distribution["tarball"])
    files = unpack_verified(data, distribution["integrity"])
    require("package.json" in files, "npm archive has no package.json")
    package = json.loads(files["package.json"])
    require(package.get("name") == "three", "Tarball package name mismatch")
    require(package.get("version") == version, "Tarball package version mismatch")
    require("LICENSE" in files, "Upstream license missing")
    selected = {name: files[name] for name in module_closure(files) + ["LICENSE"]}
    manifest = {
        "schema_version": 1,
        "entrypoints": ENTRYPOINTS,
        "upstream": {
            "name": "three",
            "version": version,
            "tarball": distribution["tarball"],
            "integrity": distribution["integrity"],
            "sha256": digest(data),
        },
        "files": {name: digest(contents) for name, contents in selected.items()},
    }
    selected["manifest.json"] = json_bytes(manifest)
    validate_assets(selected, version, modules=True)
    return selected, manifest


def commit_update(package, files, version):
    """Stage the complete update, preserving the previous tree if replacement fails."""
    assets = package / "assets"
    version_file = package / "__init__.py"
    old_version = version_file.read_bytes()
    new_version = VERSION_LINE.sub(f'__version__ = "{version}"', old_version.decode()).encode()
    with tempfile.TemporaryDirectory(prefix="threejs-update-", dir=package.parent) as temporary:
        stage = Path(temporary)
        replacement = stage / "assets"
        backup = stage / "previous-assets"
        replacement.mkdir()
        for name, contents in files.items():
            destination = replacement / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents)
        staged_version = stage / "__init__.py"
        staged_version.write_bytes(new_version)
        had_assets = assets.exists()
        if had_assets:
            assets.rename(backup)
        try:
            replacement.rename(assets)
            os.replace(staged_version, version_file)
        except BaseException:
            if assets.exists():
                assets.rename(stage / "failed-assets")
            if had_assets:
                backup.rename(assets)
            version_file.write_bytes(old_version)
            raise


def synchronize(root=ROOT, requested=None):
    package = root / "src" / "threejs_viewer_assets"
    assets = package / "assets"
    previous = None
    old_files = {}
    package_version = read_version(package)
    if assets.exists():
        old_files = read_tree(assets)
        # Validate the previous snapshot against its recorded scope before
        # regenerating it with the current entrypoints. Hash checks stay mandatory.
        recorded_entrypoints = json.loads(old_files["manifest.json"])["entrypoints"]
        previous = validate_assets(
            old_files, package_version, modules=True, entrypoints=recorded_entrypoints
        )
    target = requested
    if target is None:
        target = json.loads(fetch(f"{REGISTRY}/-/package/three/dist-tags"))["latest"]
    version_tuple(target)
    if previous:
        current = previous["upstream"]["version"]
        comparison = version_tuple(target) < version_tuple(current)
        require(
            not (requested and comparison), f"Downgrade from {current} to {target} is forbidden"
        )
        if not requested and version_tuple(target) <= version_tuple(current):
            if previous["entrypoints"] == ENTRYPOINTS:
                return False, previous, previous
            target = current
    files, manifest = prepare(target)
    if previous and previous["upstream"]["version"] == target:
        require(
            previous["upstream"]["sha256"] == manifest["upstream"]["sha256"],
            "Previously recorded npm tarball changed for the same version",
        )
    # A same-upstream refresh must not reset a packaging-only post release.
    if not previous or previous["upstream"]["version"] != target:
        package_version = target
    validate_assets(files, package_version)
    changed = files != old_files or package_version != read_version(package)
    if changed:
        commit_update(package, files, package_version)
    return changed, previous, manifest


def report(previous, manifest):
    old_version = previous["upstream"]["version"] if previous else "none"
    new_version = manifest["upstream"]["version"]
    old = previous["files"] if previous else {}
    new = manifest["files"]
    lines = [f"Update official npm `three`: **{old_version} → {new_version}**.", ""]
    for label, names in [
        ("Added", set(new) - set(old)),
        ("Removed", set(old) - set(new)),
        ("Changed", {name for name in set(new) & set(old) if new[name] != old[name]}),
    ]:
        lines.append(
            f"{label}: " + (", ".join(f"`{name}`" for name in sorted(names)) or "none") + "."
        )
    lines += [
        "",
        "The release workflow runs cross-platform CI and offline Chromium against this exact",
        "commit before fast-forwarding main. A concurrent main change stops promotion.",
        "",
        "Compatibility scope: bundled WebGL ESM addons and uncompressed glTF; see the manifest.",
        "Passing smoke tests do not prove compatibility for every application. "
        f"[Upstream release notes](https://github.com/mrdoob/three.js/releases/tag/r{version_tuple(new_version)[1]}).",
        "",
        "PyPI publication and a GitHub Release follow successful verification and promotion.",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Exact stable npm version; defaults to dist-tags.latest")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        changed, previous, manifest = synchronize(requested=args.version or None)
        version = manifest["upstream"]["version"]
        if args.report:
            args.report.write_text(report(previous, manifest), encoding="utf-8")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(f"changed={str(changed).lower()}\nversion={version}\n")
        print(f"{'Updated' if changed else 'Unchanged'}: npm three@{version}")
    except (ValidationError, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        parser.exit(1, f"Update failed: {exc}\n")


if __name__ == "__main__":
    main()
