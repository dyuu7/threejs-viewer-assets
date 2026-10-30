# threejs-viewer-assets

[![CI](https://github.com/dyuu7/threejs-viewer-assets/actions/workflows/ci.yml/badge.svg)](https://github.com/dyuu7/threejs-viewer-assets/actions/workflows/ci.yml)

Offline Three.js WebGL resources for Python applications that build interactive
3D viewers. Bundles the official core and selected addons for camera navigation,
model display, labels, and outline highlighting, with **no Python runtime
dependencies** and no Node.js build step, npm, or CDN required by consumers.

Your application supplies the viewer logic and HTTP serving. This package
distributes static assets; it does not provide a ready-made viewer or Python bindings.

Python 3.9+ · Windows, Linux, macOS · Independent of the Three.js project

## Install

```sh
pip install threejs-viewer-assets
# or: uv add threejs-viewer-assets
```

## Use

```python
from importlib.resources import files

assets = files("threejs_viewer_assets").joinpath("assets")
module = assets.joinpath("build").joinpath("three.module.js")
javascript = module.read_text(encoding="utf-8")
```

Serve the asset directory at `/assets/` with its directory structure intact,
then use an import map:

```html
<script type="importmap">
{
  "imports": {
    "three": "/assets/build/three.module.js",
    "three/addons/": "/assets/examples/jsm/"
  }
}
</script>
<script type="module">
  import * as THREE from "three";
  import { OrbitControls } from "three/addons/controls/OrbitControls.js";
</script>
```

For a working example, run `uv run python examples/serve.py` from this checkout
and open <http://127.0.0.1:8000>. Use HTTP, not `file://`.

## Viewer scope

The supported entries are `build/three.module.js`, `build/three.core.js`, and
the addons below. Addon names refer to their matching `.js` modules under
`three/addons/`, using the upstream directory structure.

| Viewer task | Included addons |
| --- | --- |
| Rotate, zoom, and navigate orientation | `OrbitControls`, `TrackballControls`, `ViewHelper` |
| Display geometry and wide line segments | `BufferGeometryUtils`, `LineSegments2`, `LineSegmentsGeometry`, `LineMaterial` |
| Load glTF models that need no extra decoders | `GLTFLoader` |
| Add HTML labels | `CSS2DRenderer` |
| Highlight objects with outlines | `EffectComposer`, `RenderPass`, `OutlinePass`, `OutputPass` |

Upstream files are unmodified. The [manifest](src/threejs_viewer_assets/assets/manifest.json)
records the version, source, hashes, and supported `entrypoints`. Additional
modules are included to satisfy their imports; those dependency-only files may
change with upstream and are not independently supported entry points.

Requires ES modules, import maps, and WebGL2. Supply your own models and textures;
Draco, Meshopt, and KTX2/Basis decoders, WebGPU/TSL, and addons outside the list
above are not included. Selection focuses on basic viewing tasks; editor tools,
physics, and game controls are outside the scope.

## Versions and compatibility

Package versions follow the bundled Three.js version; `.postN` denotes packaging
revisions of the same upstream files. Packaging revisions preserve supported
entry points. Three.js upgrades may change APIs or require entry point changes;
check the release notes and pin a version tested with your application.

Use the bundled core and addons together. When integrating other Three.js
components, coordinate their versions and import maps so shared components use
the same core module. pip/uv resolves Python package requirements but cannot
check compatibility with JavaScript supplied elsewhere. This package's browser
tests cover its viewer example, not every downstream application.

## Migrating from threejs-assets

Starting with `threejs-viewer-assets` 0.186.1.post1, replace the `threejs-assets`
dependency with `threejs-viewer-assets` and change Python imports and
`files("threejs_assets")` calls to `threejs_viewer_assets`. Refresh your lockfile.
The two distribution names are separate packages; installing the new name does
not upgrade or remove the old package automatically.

The bundled Three.js version remains 0.186.1, with the same resource selection
and bytes. Browser imports (`three`, `three/addons/`) and paths relative to
`assets/` are unchanged. Existing releases under the old name remain available;
future releases use the new name.

[Maintainer guide](docs/maintaining.md) · [MIT license](LICENSE) ·
[Three.js license](src/threejs_viewer_assets/assets/LICENSE)
