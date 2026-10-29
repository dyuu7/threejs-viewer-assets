import base64
import hashlib
import io
import json
import tarfile
import urllib.error

import project
import pytest
import update_threejs as update
from project import (
    ENTRYPOINTS,
    ValidationError,
    json_bytes,
    module_closure,
    read_tree,
    read_version,
    source_url,
    validate_assets,
    validate_source,
    validate_versions,
    version_tuple,
)


def npm_files(version):
    files = {
        "package.json": json_bytes({"name": "three", "version": version}),
        "LICENSE": b"MIT License\nCopyright test fixture\n",
        "build/three.core.js": (
            f"const REVISION = '{version_tuple(version)[1]}'; export {{ REVISION }};"
        ).encode(),
        "build/three.module.js": b"export * from './three.core.js';",
    }
    for name in ENTRYPOINTS[2:]:
        files[name] = b"import { REVISION } from 'three'; export { REVISION };"
    return files


def tarball(files, extra=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, contents in files.items():
            info = tarfile.TarInfo("package/" + name)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
        if extra:
            archive.addfile(extra)
    return buffer.getvalue()


def integrity(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class Registry:
    def __init__(self):
        self.responses = {}
        self.calls = []

    def publish(self, version, files=None):
        data = tarball(files or npm_files(version))
        self.responses[source_url(version)] = data
        self.responses[f"{update.REGISTRY}/three/{version}"] = json_bytes(
            {
                "name": "three",
                "version": version,
                "dist": {"tarball": source_url(version), "integrity": integrity(data)},
            }
        )
        self.responses[f"{update.REGISTRY}/-/package/three/dist-tags"] = json_bytes(
            {"latest": version}
        )

    def fetch(self, url):
        self.calls.append(url)
        if url not in self.responses:
            raise ValidationError(f"npm request failed: HTTP 404: {url}")
        return self.responses[url]


@pytest.fixture
def registry(monkeypatch):
    result = Registry()
    monkeypatch.setattr(update, "fetch", result.fetch)
    return result


@pytest.fixture
def checkout(tmp_path, registry):
    package = tmp_path / "src" / "threejs_assets"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.185.0"\n')
    registry.publish("0.185.0")
    update.synchronize(tmp_path)
    registry.calls.clear()
    return tmp_path


def snapshot(checkout):
    return read_tree(checkout / "src" / "threejs_assets")


def test_new_stable_release_updates_resource_and_package_versions(checkout, registry):
    registry.publish("0.186.1")
    changed, previous, manifest = update.synchronize(checkout)
    assert changed
    assert previous["upstream"]["version"] == "0.185.0"
    assert manifest["upstream"]["version"] == "0.186.1"
    assert read_version(checkout / "src" / "threejs_assets") == "0.186.1"
    assert validate_source(root=checkout, modules=True) == manifest


def test_no_update_does_not_download_tarball(checkout, registry):
    before = snapshot(checkout)
    changed, previous, manifest = update.synchronize(checkout)
    assert not changed and previous == manifest
    assert registry.calls == [f"{update.REGISTRY}/-/package/three/dist-tags"]
    assert snapshot(checkout) == before


def test_same_version_is_idempotent_and_preserves_post_release(checkout):
    package = checkout / "src" / "threejs_assets"
    (package / "__init__.py").write_text('__version__ = "0.185.0.post2"\n')
    before = snapshot(checkout)
    assert not update.synchronize(checkout, requested="0.185.0")[0]
    assert snapshot(checkout) == before


def test_update_preserves_windows_version_file_line_endings(checkout, registry):
    package = checkout / "src" / "threejs_assets"
    version_file = package / "__init__.py"
    version_file.write_bytes(b'"""Package."""\r\n\r\n__version__ = "0.185.0"\r\n')
    registry.publish("0.186.1")
    update.synchronize(checkout)
    assert version_file.read_bytes() == b'"""Package."""\r\n\r\n__version__ = "0.186.1"\r\n'


def test_older_latest_channel_does_not_downgrade(checkout, registry):
    before = snapshot(checkout)
    registry.publish("0.184.0")
    assert not update.synchronize(checkout)[0]
    assert snapshot(checkout) == before


@pytest.mark.parametrize("latest", ["0.185.0", "0.184.0"])
def test_scope_expansion_refreshes_same_upstream_and_preserves_post_release(
    tmp_path, registry, monkeypatch, latest
):
    package = tmp_path / "src" / "threejs_assets"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.185.0"\n')
    registry.publish("0.185.0")
    with monkeypatch.context() as initial_scope:
        initial_scope.setattr(project, "ENTRYPOINTS", ENTRYPOINTS[:2])
        initial_scope.setattr(update, "ENTRYPOINTS", ENTRYPOINTS[:2])
        update.synchronize(tmp_path)
    (package / "__init__.py").write_text('__version__ = "0.185.0.post1"\n')
    registry.responses[f"{update.REGISTRY}/-/package/three/dist-tags"] = json_bytes(
        {"latest": latest}
    )
    changed, previous, manifest = update.synchronize(tmp_path)
    assert changed
    assert previous["upstream"] == manifest["upstream"]
    assert manifest["entrypoints"] == ENTRYPOINTS
    assert read_version(package) == "0.185.0.post1"
    validate_source(root=tmp_path, modules=True)
    assert not update.synchronize(tmp_path)[0]


def test_scope_reduction_removes_unused_modules(checkout, monkeypatch):
    monkeypatch.setattr(project, "ENTRYPOINTS", ENTRYPOINTS[:2])
    monkeypatch.setattr(update, "ENTRYPOINTS", ENTRYPOINTS[:2])
    changed, _, manifest = update.synchronize(checkout)
    assert changed
    assert set(manifest["files"]) == set(ENTRYPOINTS[:2]) | {"LICENSE"}
    validate_source(root=checkout, modules=True)


def test_scope_change_does_not_bypass_existing_resource_integrity(checkout, monkeypatch):
    monkeypatch.setattr(project, "ENTRYPOINTS", ENTRYPOINTS[:2])
    monkeypatch.setattr(update, "ENTRYPOINTS", ENTRYPOINTS[:2])
    assets = checkout / "src" / "threejs_assets" / "assets"
    (assets / ENTRYPOINTS[-1]).write_bytes(b"tampered module being removed from scope")
    before = snapshot(checkout)
    with pytest.raises(ValidationError, match="SHA-256 mismatch"):
        update.synchronize(checkout)
    assert snapshot(checkout) == before


@pytest.mark.parametrize("target", ["0.184.0", "0.186.0-beta.1", "latest", "0.186", "1.0.0"])
def test_invalid_targets_fail_without_mutation(checkout, target):
    before = snapshot(checkout)
    with pytest.raises(ValidationError):
        update.synchronize(checkout, requested=target)
    assert snapshot(checkout) == before


def test_missing_version_fails_without_mutation(checkout):
    before = snapshot(checkout)
    with pytest.raises(ValidationError, match="404"):
        update.synchronize(checkout, requested="0.999.0")
    assert snapshot(checkout) == before


def test_prerelease_latest_tag_is_rejected(checkout, registry):
    registry.responses[f"{update.REGISTRY}/-/package/three/dist-tags"] = json_bytes(
        {"latest": "0.186.0-rc.1"}
    )
    with pytest.raises(ValidationError, match="stable"):
        update.synchronize(checkout)


def test_integrity_mismatch_does_not_change_checkout(checkout, registry):
    before = snapshot(checkout)
    registry.publish("0.186.1")
    registry.responses[source_url("0.186.1")] += b"tampered"
    with pytest.raises(ValidationError, match="integrity mismatch"):
        update.synchronize(checkout)
    assert snapshot(checkout) == before


@pytest.mark.parametrize(
    "problem", ["version", "name", "missing_core", "dependency", "bare_import"]
)
def test_changed_upstream_structure_fails_without_mutation(checkout, registry, problem):
    before = snapshot(checkout)
    files = npm_files("0.186.1")
    if problem in {"version", "name"}:
        package = json.loads(files["package.json"])
        package[problem] = "wrong"
        files["package.json"] = json_bytes(package)
    elif problem == "missing_core":
        del files["build/three.core.js"]
    elif problem == "dependency":
        files[ENTRYPOINTS[-1]] += b"import '../utils/missing.js';"
    else:
        files[ENTRYPOINTS[-1]] += b"import 'three/webgpu';"
    registry.publish("0.186.1", files)
    with pytest.raises(ValidationError):
        update.synchronize(checkout)
    assert snapshot(checkout) == before


def test_same_version_cannot_silently_replace_recorded_tarball(checkout, registry):
    before = snapshot(checkout)
    files = npm_files("0.185.0")
    files[ENTRYPOINTS[-1]] += b"\n// changed upstream bytes"
    registry.publish("0.185.0", files)
    with pytest.raises(ValidationError, match="tarball changed"):
        update.synchronize(checkout, requested="0.185.0")
    assert snapshot(checkout) == before


def test_failed_replacement_restores_old_resources_and_version(checkout, registry, monkeypatch):
    before = snapshot(checkout)
    registry.publish("0.186.1")

    def fail_replace(*args):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(update.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        update.synchronize(checkout)
    assert snapshot(checkout) == before


def test_module_parser_follows_reexports_but_ignores_comments_and_strings():
    files = npm_files("0.186.1")
    files[ENTRYPOINTS[-1]] += b"""
        // import 'https://example.invalid/unused.js';
        const text = "from 'unrelated'";
        export * from '../utils/dependency.js';
        const url = import.meta.url;
    """
    files["examples/jsm/utils/dependency.js"] = b"export const value = 1;"
    assert set(module_closure(files)) == set(ENTRYPOINTS + ["examples/jsm/utils/dependency.js"])


@pytest.mark.parametrize(
    "statement",
    [
        "import(variable)",
        "import('https://example.invalid/remote.js')",
        "import('../../../../outside.js')",
        "import('./local.js?query=1')",
    ],
)
def test_unresolvable_module_imports_fail_closed(statement):
    files = npm_files("0.186.1")
    files[ENTRYPOINTS[-1]] += statement.encode() + b";"
    with pytest.raises(ValidationError):
        module_closure(files)


@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "hardlink", "duplicate"])
def test_unsafe_tar_members_are_rejected(kind):
    info = tarfile.TarInfo("package/extra")
    if kind == "traversal":
        info.name = "package/../../outside"
    elif kind == "absolute":
        info.name = "/outside"
    elif kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = "/outside"
    elif kind == "hardlink":
        info.type = tarfile.LNKTYPE
        info.linkname = "package/LICENSE"
    else:
        info.name = "package/LICENSE"
    data = tarball(npm_files("0.186.1"), extra=info)
    with pytest.raises(ValidationError):
        update.unpack_verified(data, integrity(data))


def test_oversized_archive_is_rejected(monkeypatch):
    monkeypatch.setattr(update, "MAX_UNPACKED", 10)
    data = tarball(npm_files("0.186.1"))
    with pytest.raises(ValidationError, match="large"):
        update.unpack_verified(data, integrity(data))


def test_network_error_is_clear(monkeypatch):
    class FailingOpener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError("offline")

    monkeypatch.setattr(update.urllib.request, "build_opener", lambda *args: FailingOpener())
    with pytest.raises(ValidationError, match="npm request failed"):
        update.fetch("https://registry.npmjs.org/three/0.186.1")


@pytest.mark.parametrize(
    "url",
    [
        "http://registry.npmjs.org/three",
        "https://registry.npmjs.org.evil.test/three",
        "https://user@registry.npmjs.org/three",
        "https://example.com/three.tgz",
    ],
)
def test_nonofficial_downloads_and_redirects_are_rejected(url):
    with pytest.raises(ValidationError, match="Nonofficial"):
        update.official_url(url)
    with pytest.raises(ValidationError, match="Nonofficial"):
        update.RegistryRedirects().redirect_request(None, None, 302, "", {}, url)


@pytest.mark.parametrize(
    "package,upstream",
    [
        ("0.186.1", "0.186.1"),
        ("0.186.1.post0", "0.186.1"),
        ("0.186.1.post2", "0.186.1"),
    ],
)
def test_package_and_upstream_version_relationship(package, upstream):
    validate_versions(package, upstream)


@pytest.mark.parametrize("package", ["0.186.0", "0.187.0", "0.186.1.dev1", "0.186.1+local"])
def test_mismatched_python_releases_are_rejected(package):
    with pytest.raises(ValidationError):
        validate_versions(package, "0.186.1")


def test_manifest_detects_modified_missing_and_extra_resources(checkout):
    assets = read_tree(checkout / "src" / "threejs_assets" / "assets")
    for altered in [
        dict(assets, **{"LICENSE": b"changed"}),
        {name: data for name, data in assets.items() if name != ENTRYPOINTS[-1]},
        dict(assets, **{"unexpected.bin": b"extra"}),
    ]:
        with pytest.raises(ValidationError):
            validate_assets(altered, "0.185.0")


def test_real_vendored_resources_satisfy_policy():
    manifest = validate_source(modules=True)
    assert manifest["upstream"]["name"] == "three"
