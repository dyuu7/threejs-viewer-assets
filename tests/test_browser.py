"""Exercise only resources served from a wheel installed into an empty venv."""

import json
import os
import queue
import subprocess
import threading

import pytest
from project import ROOT, version_tuple

pytestmark = pytest.mark.browser


@pytest.fixture
def installed_server(tmp_path):
    python = os.environ.get("THREEJS_INSTALLED_PYTHON")
    assert python, "Run scripts/check_dist.py --browser to test an isolated wheel installation"
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [python, "-I", str(ROOT / "examples" / "serve.py"), "--port", "0"],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            env=dict(os.environ, PATH=""),
        )
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = ready.get(timeout=20)
            assert line, "Example HTTP server failed to start"
            yield json.loads(line)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            process.stdout.close()


def test_offline_rendering_and_supported_addons(installed_server):
    from playwright.sync_api import sync_playwright

    origin = installed_server["url"]
    errors = []
    external = []
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            viewport={"width": 960, "height": 720}, service_workers="block"
        )

        def route_request(route):
            url = route.request.url
            if not url.startswith(origin + "/"):
                external.append(url)
                route.abort()
            else:
                requests.append(url)
                route.continue_()

        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.on(
            "requestfailed",
            lambda request: errors.append(f"Failed: {request.url}: {request.failure}"),
        )
        page.on(
            "response",
            lambda response: (
                errors.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400
                else None
            ),
        )
        try:
            page.goto(origin + "/", wait_until="networkidle")
            page.wait_for_function("window.demo && demo.frames >= 5")
            assert page.evaluate("demo.revision") == str(
                version_tuple(installed_server["upstream_version"])[1]
            )
            assert page.evaluate(
                "demo.scene.isScene && demo.camera.isPerspectiveCamera"
                " && demo.renderer.isWebGLRenderer"
            )
            assert page.evaluate("demo.model.children[0].geometry.attributes.position.count") == 3
            assert page.evaluate("demo.cube.geometry.index.count") == 72
            assert page.evaluate(
                "demo.line.isLineSegments2 && demo.line.geometry.isLineSegmentsGeometry"
                " && demo.line.material.isLineMaterial && demo.view.isViewHelper"
            )
            assert page.locator("#labels .label").is_visible()
            assert page.locator("#labels .label").inner_text() == "Merged geometry"
            assert page.evaluate("demo.renderer.info.render.calls") >= 2
            colored_pixels = page.evaluate("""() => {
                const gl = demo.renderer.getContext();
                const pixels = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
                gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight,
                    gl.RGBA, gl.UNSIGNED_BYTE, pixels);
                let colored = 0;
                for (let i = 0; i < pixels.length; i += 4) {
                    if (pixels[i] > 60 || pixels[i + 1] > 60 || pixels[i + 2] > 60) colored++;
                }
                return colored;
            }""")
            assert colored_pixels > 500, "WebGL drew no visible geometry"
            angle = page.evaluate("demo.cube.rotation.y")
            page.wait_for_function("angle => demo.cube.rotation.y > angle + 0.02", arg=angle)
            for controls in ["orbit", "trackball"]:
                page.select_option("#controls", controls)
                position = page.evaluate("demo.camera.position.toArray()")
                page.mouse.move(480, 450)
                page.mouse.down()
                page.mouse.move(620, 510, steps=10)
                page.mouse.up()
                page.wait_for_function(
                    "previous => demo.camera.position.toArray()"
                    ".some((x, i) => Math.abs(x - previous[i]) > 0.01)",
                    arg=position,
                )
            # Click a real ViewHelper axis and wait for its camera animation.
            page.select_option("#controls", "orbit")
            axis = page.evaluate("""() => {
                demo.camera.position.set(4, 3, 7);
                demo.render();
                const sprite = demo.view.children.find(child => child.userData.type === 'posX');
                const point = sprite.getWorldPosition(sprite.position.clone());
                const rect = demo.renderer.domElement.getBoundingClientRect();
                return { x: rect.right - 64 + point.x * 32, y: rect.bottom - 64 - point.y * 32 };
            }""")
            page.mouse.click(axis["x"], axis["y"])
            page.wait_for_function(
                "!demo.view.animating && demo.camera.position.x > 0"
                " && Math.abs(demo.camera.position.y) < 0.01"
                " && Math.abs(demo.camera.position.z) < 0.01"
            )
            # Resize all renderers and targets, then compare actual rendered pixels
            # with each effect disabled. Imports/constructors alone are insufficient.
            page.set_viewport_size({"width": 800, "height": 600})
            page.wait_for_function(
                "demo.renderer.domElement.width === 800 && demo.composer.readBuffer.width === 800"
            )
            differences = page.evaluate("""() => {
                demo.renderer.setAnimationLoop(null);
                demo.camera.position.set(4, 3, 7);
                demo.camera.lookAt(0, 0, 0);
                demo.orbit.enabled = demo.trackball.enabled = false;
                const gl = demo.renderer.getContext();
                function capture() {
                    demo.render();
                    const pixels = new Uint8Array(
                        gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
                    gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight,
                        gl.RGBA, gl.UNSIGNED_BYTE, pixels);
                    return pixels;
                }
                const baseline = capture();
                const differences = {};
                for (const [name, object, property] of [
                    ['outline', demo.outline, 'enabled'],
                    ['wideLine', demo.line, 'visible'],
                    ['viewHelper', demo.view, 'visible'],
                ]) {
                    object[property] = false;
                    const pixels = capture();
                    let count = 0;
                    for (let i = 0; i < pixels.length; i += 4) {
                        if (Math.abs(pixels[i] - baseline[i]) > 10 ||
                            Math.abs(pixels[i + 1] - baseline[i + 1]) > 10 ||
                            Math.abs(pixels[i + 2] - baseline[i + 2]) > 10) count++;
                    }
                    differences[name] = count;
                    object[property] = true;
                }
                capture();
                return differences;
            }""")
            assert all(count > 50 for count in differences.values()), differences
            label_box = page.locator("#labels .label").bounding_box()
            assert label_box and 0 < label_box["x"] < 800 and 0 < label_box["y"] < 600
            destination = ROOT / "test-results"
            destination.mkdir(exist_ok=True)
            page.screenshot(path=str(destination / "offline-demo.png"))
            assert any(url.endswith("/triangle.gltf") for url in requests)
            assert any(url.endswith("/assets/build/three.core.js") for url in requests)
            assert not external, f"External network attempts: {external}"
            assert not errors, f"Browser errors: {errors}"
        finally:
            context.close()
            browser.close()
