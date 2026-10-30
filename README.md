# threejs-viewer-assets

[![CI](https://github.com/dyuu7/threejs-viewer-assets/actions/workflows/ci.yml/badge.svg)](https://github.com/dyuu7/threejs-viewer-assets/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/threejs-viewer-assets)](https://pypi.org/project/threejs-viewer-assets/)

Offline Three.js WebGL resources for Python applications that build interactive
3D viewers. Bundles the official core and selected addons for camera navigation,
model display, labels, and outline highlighting, with **no Python runtime
dependencies** and no Node.js build step, npm, or CDN required by consumers.

Access the static files through Python and serve them over HTTP to your viewer.

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
and open <http://127.0.0.1:8000>.

## Viewer scope

The supported entries are `build/three.module.js`, `build/three.core.js`, and
the addons below. Addon names refer to their matching `.js` modules under
`three/addons/`, using the upstream directory structure.

| Viewer task | Included addons |
| --- | --- |
| Rotate, zoom, and navigate orientation | `OrbitControls`, `TrackballControls`, `ViewHelper` |
| Display geometry and wide line segments | `BufferGeometryUtils`, `LineSegments2`, `LineSegmentsGeometry`, `LineMaterial` |
| Load uncompressed glTF models | `GLTFLoader` |
| Add HTML labels | `CSS2DRenderer` |
| Highlight objects with outlines | `EffectComposer`, `RenderPass`, `OutlinePass`, `OutputPass` |

Upstream files are unmodified. The [manifest](src/threejs_viewer_assets/assets/manifest.json)
records the version, source, hashes, and supported `entrypoints`. Its `files`
list includes the complete module dependency closure for those entries.

Use a browser with ES modules, import maps, and WebGL2, and supply your
application's models and textures.

## Versions and compatibility

Package versions follow the bundled Three.js version; `.postN` denotes packaging
revisions of the same upstream files. Pin a version tested with your application
and review the Three.js release notes when upgrading.

Use the bundled core and addons together. When integrating other Three.js
components, coordinate their versions and import maps so shared components use
the same core module. pip/uv resolves Python package requirements; compatibility
with JavaScript supplied elsewhere is managed by your application.

[Maintainer guide](docs/maintaining.md) · [MIT license](LICENSE) ·
[Three.js license](src/threejs_viewer_assets/assets/LICENSE)
