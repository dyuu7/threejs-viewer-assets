import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import { ViewHelper } from 'three/addons/helpers/ViewHelper.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutlinePass } from 'three/addons/postprocessing/OutlinePass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const scene = new THREE.Scene();
scene.background = new THREE.Color('#0b0f18');
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 100);
camera.position.set(4, 3, 7);
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);

// Merging two boxes exercises BufferGeometryUtils while keeping one mesh.
const cube = new THREE.Mesh(mergeGeometries([
  new THREE.BoxGeometry(),
  new THREE.BoxGeometry(0.35, 0.35, 0.35).translate(0, 0.675, 0),
]), new THREE.MeshNormalMaterial());
scene.add(cube);
scene.add(new THREE.HemisphereLight(0xffffff, 0x8090aa, 3));
const lineGeometry = new LineSegmentsGeometry().setPositions([
  -1.4, -0.8, -1.4, 1.4, -0.8, -1.4,
  1.4, -0.8, -1.4, 1.4, -0.8, 1.4,
  1.4, -0.8, 1.4, -1.4, -0.8, 1.4,
  -1.4, -0.8, 1.4, -1.4, -0.8, -1.4,
]);
const line = new LineSegments2(lineGeometry, new LineMaterial({ color: 0x32d6e0, linewidth: 5 }));
line.computeLineDistances();
scene.add(line);

const orbit = new OrbitControls(camera, renderer.domElement);
const trackball = new TrackballControls(camera, renderer.domElement);
trackball.enabled = false;
let active = orbit;
document.querySelector('#controls').addEventListener('change', event => {
  orbit.enabled = event.target.value === 'orbit';
  trackball.enabled = !orbit.enabled;
  active = orbit.enabled ? orbit : trackball;
});

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const outline = new OutlinePass(new THREE.Vector2(innerWidth, innerHeight), scene, camera, [cube]);
outline.visibleEdgeColor.set('#ffbf69');
outline.hiddenEdgeColor.set('#ffbf69');
outline.edgeStrength = 4;
composer.addPass(outline);
composer.addPass(new OutputPass());
document.querySelector('#outline').addEventListener('change', event => {
  outline.enabled = event.target.checked;
});

const labels = new CSS2DRenderer();
labels.setSize(innerWidth, innerHeight);
labels.domElement.id = 'labels';
document.body.appendChild(labels.domElement);
const caption = document.createElement('span');
caption.className = 'label';
caption.textContent = 'Merged geometry';
const label = new CSS2DObject(caption);
label.position.set(0, 1.25, 0);
scene.add(label);

const view = new ViewHelper(camera, renderer.domElement);
renderer.domElement.addEventListener('pointerup', event => view.handleClick(event));

const gltf = await new GLTFLoader().loadAsync('/triangle.gltf');
gltf.scene.position.x = 2;
scene.add(gltf.scene);
const response = await fetch('/assets/manifest.json');
if (!response.ok) throw new Error(`Manifest HTTP ${response.status}`);
const manifest = await response.json();
document.querySelector('#status').textContent = `Three.js ${manifest.upstream.version} · all resources local`;

// Exposed only by this example so the browser test can inspect actual rendering.
const demo = window.demo = { scene, camera, renderer, cube, orbit, trackball, model: gltf.scene,
  line, composer, outline, labels, label, view, render, revision: THREE.REVISION, frames: 0 };
function render(delta = 0) {
  if (view.animating) {
    view.update(delta);
  } else {
    active.update();
  }
  renderer.info.reset();
  renderer.info.autoReset = false;
  composer.render(delta);
  renderer.autoClear = false;
  view.render(renderer);
  renderer.autoClear = true;
  labels.render(scene, camera);
}
let previousTime;
renderer.setAnimationLoop(time => {
  const delta = previousTime === undefined ? 0 : Math.min((time - previousTime) / 1000, 0.1);
  previousTime = time;
  cube.rotation.y += delta * 0.5;
  cube.rotation.x += delta * 0.2;
  render(delta);
  demo.frames++;
});
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  composer.setSize(innerWidth, innerHeight);
  labels.setSize(innerWidth, innerHeight);
  trackball.handleResize();
});
