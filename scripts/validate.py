"""Validate source resources, distributions, or an isolated installed wheel."""

import argparse
import base64
import csv
import hashlib
import io
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

# -I deliberately excludes the checkout from imports; only these maintenance
# helpers are added back, never src/ or the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import (  # noqa: E402
    PACKAGE,
    ROOT,
    ValidationError,
    read_tree,
    read_version,
    require,
    validate_assets,
    validate_source,
)

SDIST_FILES = {
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    ".npmrc",
    "README.md",
    "LICENSE",
}
SDIST_DIRS = {"src", "scripts", "tests", "examples", "docs"}


def metadata_check(contents, version):
    metadata = BytesParser().parsebytes(contents)
    require(metadata["Name"] == "threejs-viewer-assets", "Incorrect distribution name")
    require(metadata["Version"] == version, "Incorrect distribution version")
    require(not metadata.get_all("Requires-Dist"), "Runtime dependencies must be empty")
    require(metadata["Requires-Python"] == ">=3.9", "Incorrect Python support metadata")
    require(metadata["License-Expression"] == "MIT", "Missing MIT license declaration")
    require(metadata.get_all("License-File") == ["LICENSE"], "Unexpected declared license files")


def wheel_files(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate wheel entries")
        return {name: archive.read(name) for name in names}


def validate_distributions(directory, root=ROOT):
    version = read_version(root / "src" / "threejs_viewer_assets")
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    require(len(wheels) == len(sdists) == 1, "Expected exactly one wheel and one sdist")
    wheel, sdist = wheels[0], sdists[0]
    require(
        wheel.name == f"threejs_viewer_assets-{version}-py3-none-any.whl",
        "Incorrect wheel filename",
    )
    require(sdist.name == f"threejs_viewer_assets-{version}.tar.gz", "Incorrect sdist filename")
    package = root / "src" / "threejs_viewer_assets"
    expected = {
        f"threejs_viewer_assets/assets/{name}": data
        for name, data in read_tree(package / "assets").items()
    }
    expected["threejs_viewer_assets/__init__.py"] = (package / "__init__.py").read_bytes()
    contents = wheel_files(wheel)
    info = f"threejs_viewer_assets-{version}.dist-info/"
    metadata_names = {info + name for name in ("METADATA", "WHEEL", "RECORD", "licenses/LICENSE")}
    require(set(contents) == set(expected) | metadata_names, "Unexpected or missing wheel files")
    for name, data in expected.items():
        require(contents[name] == data, f"Wheel/source mismatch: {name}")
    require(
        contents[info + "licenses/LICENSE"] == (root / "LICENSE").read_bytes(), "License mismatch"
    )
    metadata_check(contents[info + "METADATA"], version)
    wheel_metadata = BytesParser().parsebytes(contents[info + "WHEEL"])
    require(wheel_metadata.get_all("Tag") == ["py3-none-any"], "Incorrect wheel compatibility tags")
    require(wheel_metadata["Root-Is-Purelib"] == "true", "Wheel must contain only pure data/Python")
    rows = list(csv.reader(io.StringIO(contents[info + "RECORD"].decode())))
    require(
        len(rows) == len(contents) and {row[0] for row in rows} == set(contents), "Invalid RECORD"
    )
    for name, checksum, size in rows:
        if name == info + "RECORD":
            require(checksum == size == "", "RECORD must not hash itself")
        else:
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(contents[name]).digest())
                .rstrip(b"=")
                .decode()
            )
            require(
                checksum == "sha256=" + actual and size == str(len(contents[name])),
                f"Bad RECORD: {name}",
            )
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        prefix = f"threejs_viewer_assets-{version}/"
        source = {}
        for member in members:
            require(
                member.isfile() and member.name.startswith(prefix),
                f"Invalid sdist entry: {member.name}",
            )
            name = member.name[len(prefix) :]
            require(name not in source, f"Duplicate sdist entry: {name}")
            require(".." not in name.split("/") and "\\" not in name, "Unsafe sdist path")
            require(
                name in SDIST_FILES | {"PKG-INFO"} or name.split("/")[0] in SDIST_DIRS,
                f"Unexpected sdist file: {name}",
            )
            require(
                not any(
                    part.startswith(".") or part in {"__pycache__", "node_modules"}
                    for part in name.split("/")
                )
                or name in {".npmrc", ".gitignore"},
                f"Cache/hidden file in sdist: {name}",
            )
            require(not name.endswith((".pyc", ".pyo")), f"Bytecode in sdist: {name}")
            source[name] = archive.extractfile(member).read()
    require(SDIST_FILES <= set(source), "Required sdist configuration missing")
    for name, data in expected.items():
        require(source.get("src/" + name) == data, f"sdist/source mismatch: {name}")
    for name in SDIST_FILES:
        require(source[name] == (root / name).read_bytes(), f"sdist config differs: {name}")
    metadata_check(source["PKG-INFO"], version)
    print(f"Validated {wheel.name} ({wheel.stat().st_size:,} bytes) and {sdist.name}")
    return wheel, sdist


def validate_installed():
    import importlib.metadata
    import importlib.resources

    import threejs_viewer_assets

    distribution = importlib.metadata.distribution("threejs-viewer-assets")
    require(not distribution.requires, "Installed package has runtime dependencies")
    require(distribution.version == threejs_viewer_assets.__version__, "Installed version mismatch")
    require(
        not Path(threejs_viewer_assets.__file__).resolve().is_relative_to(PACKAGE),
        "Imported checkout",
    )
    assets = importlib.resources.files("threejs_viewer_assets").joinpath("assets")
    manifest = validate_assets(read_tree(assets), distribution.version)
    print(f"Installed {distribution.version}: {len(manifest['files'])} verified upstream files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--modules", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        if args.installed:
            validate_installed()
            return
        manifest = validate_source(modules=args.modules)
        version = read_version()
        if args.tag:
            require(args.tag == f"v{version}", f"Tag {args.tag!r} does not match package {version}")
        if args.artifacts:
            validate_distributions(args.artifacts)
        print(f"Verified package {version}, upstream three@{manifest['upstream']['version']}")
    except (
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        parser.exit(1, f"Validation failed: {exc}\n")


if __name__ == "__main__":
    main()
