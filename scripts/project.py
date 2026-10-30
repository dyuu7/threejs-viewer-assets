"""Shared distribution policy and validation, kept out of the installed package."""

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "threejs_viewer_assets"
# Public viewer entry points; keep README scope and browser coverage aligned.
# The remaining module closure is an implementation dependency, not a public API.
ENTRYPOINTS = [
    "build/three.core.js",
    "build/three.module.js",
    "examples/jsm/controls/OrbitControls.js",
    "examples/jsm/controls/TrackballControls.js",
    "examples/jsm/helpers/ViewHelper.js",
    "examples/jsm/lines/LineMaterial.js",
    "examples/jsm/lines/LineSegments2.js",
    "examples/jsm/lines/LineSegmentsGeometry.js",
    "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/postprocessing/EffectComposer.js",
    "examples/jsm/postprocessing/OutlinePass.js",
    "examples/jsm/postprocessing/OutputPass.js",
    "examples/jsm/postprocessing/RenderPass.js",
    "examples/jsm/renderers/CSS2DRenderer.js",
    "examples/jsm/utils/BufferGeometryUtils.js",
]
SEMVER = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
VERSION_LINE = re.compile(r'^__version__ = "([^"\r\n]+)"(?=\r?$)', re.MULTILINE)


class ValidationError(ValueError):
    """An input or artifact violates the project's distribution contract."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def version_tuple(version):
    require(isinstance(version, str), "Version must be a string")
    match = re.fullmatch(SEMVER, version)
    require(match is not None, f"Expected an exact stable npm version, got {version!r}")
    values = tuple(int(part) for part in match.groups())
    require(values[0] == 0, "Three.js major version convention changed; manual review required")
    return values


def validate_versions(package_version, upstream_version):
    version_tuple(upstream_version)
    require(
        re.fullmatch(re.escape(upstream_version) + r"(?:\.post(?:0|[1-9]\d*))?", package_version)
        is not None,
        f"Package {package_version} does not correspond to npm three@{upstream_version}",
    )


def read_version(package=PACKAGE):
    match = VERSION_LINE.search((package / "__init__.py").read_text(encoding="utf-8"))
    require(match is not None, "Missing canonical __version__ assignment")
    return match.group(1)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def source_url(version):
    version_tuple(version)
    return f"https://registry.npmjs.org/three/-/three-{version}.tgz"


def read_tree(root):
    """Read a pathlib directory or importlib.resources Traversable without copying it."""
    result = {}

    def visit(directory, prefix):
        for child in directory.iterdir():
            name = prefix + child.name
            if isinstance(child, Path):
                require(not child.is_symlink(), f"Symlink in resources: {name}")
            if child.is_dir():
                visit(child, name + "/")
            else:
                require(child.is_file(), f"Not a regular resource: {name}")
                result[name] = child.read_bytes()

    visit(root, "")
    return result


def module_closure(files, *, entrypoints=None):
    entrypoints = ENTRYPOINTS if entrypoints is None else entrypoints
    modules = {path: data.decode("utf-8") for path, data in files.items() if path.endswith(".js")}
    try:
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / "js_dependencies.mjs")],
            input=json.dumps({"files": modules, "entrypoints": entrypoints}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        details = getattr(exc, "stderr", "") or str(exc)
        raise ValidationError(f"ESM dependency validation failed (run npm ci): {details}") from exc
    return json.loads(result.stdout)


def validate_assets(files, package_version, *, modules=False, entrypoints=None):
    entrypoints = ENTRYPOINTS if entrypoints is None else entrypoints
    require("manifest.json" in files, "Missing resource manifest")
    manifest = json.loads(files["manifest.json"])
    require(manifest.get("schema_version") == 1, "Unsupported resource manifest schema")
    upstream = manifest["upstream"]
    version = upstream["version"]
    validate_versions(package_version, version)
    require(upstream["name"] == "three", "Unexpected upstream npm package")
    require(upstream["tarball"] == source_url(version), "Unexpected upstream tarball URL")
    require(re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", upstream["integrity"]), "Invalid SRI")
    require(re.fullmatch(r"[a-f0-9]{64}", upstream["sha256"]), "Invalid tarball SHA-256")
    require(manifest["entrypoints"] == entrypoints, "Resource policy differs from entrypoints")
    expected = manifest["files"]
    require(
        set(files) == set(expected) | {"manifest.json"}, "Resource file set differs from manifest"
    )
    require(set(entrypoints + ["LICENSE"]) <= set(expected), "Required resources missing")
    for name, checksum in expected.items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts, f"Unsafe resource: {name}")
        require(
            name == "LICENSE"
            or (name.endswith(".js") and name.startswith(("build/", "examples/jsm/"))),
            f"Unexpected resource: {name}",
        )
        require(digest(files[name]) == checksum, f"SHA-256 mismatch: {name}")
    core = files["build/three.core.js"].decode("utf-8")
    revision = re.search(r"\bconst REVISION = ['\"]([^'\"]+)['\"];", core)
    require(revision is not None, "Missing upstream REVISION")
    require(revision.group(1) == str(version_tuple(version)[1]), "Upstream REVISION mismatch")
    require(b"MIT License" in files["LICENSE"], "Missing upstream MIT license")
    if modules:
        require(
            set(module_closure(files, entrypoints=entrypoints)) | {"LICENSE"} == set(expected),
            "Module closure differs",
        )
    return manifest


def validate_source(*, modules=False, root=ROOT):
    package = root / "src" / "threejs_viewer_assets"
    version = read_version(package)
    return validate_assets(read_tree(package / "assets"), version, modules=modules)
