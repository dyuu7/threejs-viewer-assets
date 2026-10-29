# threejs-assets

[![CI](https://github.com/dyuu7/threejs-assets/actions/workflows/ci.yml/badge.svg)](https://github.com/dyuu7/threejs-assets/actions/workflows/ci.yml)

Three.js ES modules in a Python wheel. Use the official files offline with
**zero runtime dependencies**—no Node.js, npm, or CDN needed.

Python 3.9+ · Windows, Linux, macOS · Independent of the Three.js project

## Install

```sh
pip install threejs-assets
# or: uv add threejs-assets
```

Not yet published to PyPI. Until the first release, build with `uv build` and
install the wheel from `dist/`.

## Use

```python
from importlib.resources import files

assets = files("threejs_assets").joinpath("assets")
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

## Scope

Bundles Three.js core ESM and these addons, with their module dependencies:

- `OrbitControls`, `TrackballControls`, `ViewHelper`
- `LineSegments2`, `LineSegmentsGeometry`, `LineMaterial`
- `EffectComposer`, `RenderPass`, `OutlinePass`, `OutputPass`
- `CSS2DRenderer`, `BufferGeometryUtils`, `GLTFLoader`

Upstream files are unmodified. The [manifest](src/threejs_assets/assets/manifest.json)
records the version, source, and hashes; the initial release uses **Three.js 0.186.1**.

Requires ES modules, import maps, and WebGL2. Supply your own models and textures;
compressed-model decoders, WebGPU/TSL, and other addons are not included. This is
static asset distribution, not a Python binding or web server.

[Maintainer guide](docs/maintaining.md) · [MIT license](LICENSE) ·
[Three.js license](src/threejs_assets/assets/LICENSE)
