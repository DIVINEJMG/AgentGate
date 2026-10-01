import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export type ControlWorld = {
  resize: (width: number, height: number) => void;
  render: (progress: number, pointerX: number, pointerY: number, time: number, motion: boolean) => void;
  dispose: () => void;
};

const clamp = (value: number) => Math.min(1, Math.max(0, value));
const smooth = (from: number, to: number, value: number) => {
  const t = clamp((value - from) / (to - from));
  return t * t * (3 - 2 * t);
};

export function createControlWorld(canvas: HTMLCanvasElement): ControlWorld {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.6;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, .1, 60);
  const cameraTarget = new THREE.Vector3(0, .08, 0);
  const sculpture = new THREE.Group();
  scene.add(sculpture);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x9ca99a, 2.4));
  const key = new THREE.DirectionalLight(0xfffdf3, 4.3);
  key.position.set(-3, 7, 5);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = -7;
  key.shadow.camera.right = 7;
  key.shadow.camera.top = 7;
  key.shadow.camera.bottom = -7;
  key.shadow.bias = -.00015;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xb1c4b1, 3.2);
  rim.position.set(4, 3, -5);
  scene.add(rim);

  const porcelain = new THREE.MeshStandardMaterial({ color: 0xe5e9df, roughness: .78, metalness: .06 });
  const paleEdge = new THREE.MeshStandardMaterial({ color: 0xb9c7ba, roughness: .43, metalness: .3 });
  const darkMetal = new THREE.MeshStandardMaterial({ color: 0x26382f, roughness: .36, metalness: .44 });
  const satinGreen = new THREE.MeshStandardMaterial({ color: 0x3d5b49, roughness: .28, metalness: .28 });
  const lightInset = new THREE.MeshStandardMaterial({ color: 0xd5e2d1, roughness: .42, metalness: .06 });

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(22, 22),
    new THREE.MeshStandardMaterial({ color: 0xf7f7f3, roughness: 1 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -1.88;
  floor.receiveShadow = true;
  scene.add(floor);

  const plinth = new THREE.Mesh(new RoundedBoxGeometry(5.7, .16, 6.7, 3, .06), porcelain);
  plinth.position.set(0, -1.82, -.15);
  plinth.receiveShadow = true;
  sculpture.add(plinth);

  // Architectural thresholds are physical objects, never authorization logic.
  const thresholds: THREE.Group[] = [];
  const gateDepths = [1.55, .15, -1.25];
  gateDepths.forEach((depth, index) => {
    const gate = new THREE.Group();
    gate.position.z = depth;
    const material = index === 0 ? paleEdge : index === 1 ? darkMetal : satinGreen;
    const width = index === 0 ? 2.88 : 2.62;
    const height = index === 0 ? 3.45 : 3.28;
    const sideGeometry = new RoundedBoxGeometry(.17, height, .23, 3, .045);
    const topGeometry = new RoundedBoxGeometry(width, .17, .23, 3, .045);
    const left = new THREE.Mesh(sideGeometry, material);
    const right = new THREE.Mesh(sideGeometry, material);
    const top = new THREE.Mesh(topGeometry, material);
    const bottom = new THREE.Mesh(topGeometry, material);
    left.position.x = -width / 2;
    right.position.x = width / 2;
    top.position.y = height / 2;
    bottom.position.y = -height / 2;
    [left, right, top, bottom].forEach((part) => { part.castShadow = true; part.receiveShadow = true; gate.add(part); });
    const foot = new THREE.Mesh(new RoundedBoxGeometry(width + .32, .12, .55, 2, .03), porcelain);
    foot.position.y = -height / 2 - .08;
    foot.receiveShadow = true;
    gate.add(foot);
    sculpture.add(gate);
    thresholds.push(gate);
  });

  const approval = new THREE.Group();
  approval.position.set(0, 0, -1.15);
  const leafGeometry = new RoundedBoxGeometry(1.1, 2.86, .1, 3, .035);
  const leftLeaf = new THREE.Mesh(leafGeometry, porcelain);
  const rightLeaf = new THREE.Mesh(leafGeometry, porcelain);
  leftLeaf.position.x = -.57;
  rightLeaf.position.x = .57;
  leftLeaf.castShadow = true;
  rightLeaf.castShadow = true;
  approval.add(leftLeaf, rightLeaf);
  sculpture.add(approval);

  const work = new THREE.Group();
  const body = new THREE.Mesh(new RoundedBoxGeometry(1.25, .91, .36, 4, .09), darkMetal);
  body.castShadow = true;
  body.receiveShadow = true;
  work.add(body);
  const face = new THREE.Mesh(new RoundedBoxGeometry(1.04, .7, .028, 3, .035), satinGreen);
  face.position.z = .19;
  work.add(face);
  const inset = new THREE.Mesh(new RoundedBoxGeometry(.82, .13, .017, 2, .008), lightInset);
  inset.position.set(-.01, .16, .211);
  work.add(inset);
  const secondary = new THREE.Mesh(new RoundedBoxGeometry(.53, .045, .014, 2, .005), paleEdge);
  secondary.position.set(-.15, -.04, .215);
  work.add(secondary);
  for (let index = 0; index < 3; index += 1) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(.026, 10, 8), lightInset);
    dot.position.set(-.32 + index * .12, -.24, .218);
    work.add(dot);
  }
  sculpture.add(work);

  const route = new THREE.Mesh(
    new THREE.CylinderGeometry(.014, .014, 6.3, 10),
    new THREE.MeshStandardMaterial({ color: 0x8ba38d, roughness: .4, metalness: .45 }),
  );
  route.rotation.x = Math.PI / 2;
  route.position.set(0, -.87, -.15);
  sculpture.add(route);
  const marker = new THREE.Mesh(new THREE.SphereGeometry(.055, 14, 10), lightInset);
  marker.position.set(0, -.87, -3.2);
  sculpture.add(marker);

  function resize(width: number, height: number) {
    if (!width || !height) return;
    camera.aspect = width / height;
    camera.fov = width < 560 ? 43 : 36;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
  }

  function render(progress: number, pointerX: number, pointerY: number, time: number, motion: boolean) {
    const p = clamp(progress);
    const passingPolicy = smooth(.18, .5, p);
    const approvalOpen = smooth(.55, .76, p);
    const exit = smooth(.72, 1, p);
    const float = motion ? Math.sin(time * 1.1) * .035 : 0;

    work.position.set(0, .15 + float, THREE.MathUtils.lerp(2.45, -.62, passingPolicy) - exit * 2.25);
    work.rotation.y = -.13 + p * .2 + (motion ? Math.sin(time * .45) * .025 : 0);
    work.rotation.x = -.04 + passingPolicy * .1;
    leftLeaf.position.x = -.57 - approvalOpen * .96;
    rightLeaf.position.x = .57 + approvalOpen * .96;
    leftLeaf.rotation.y = -approvalOpen * .38;
    rightLeaf.rotation.y = approvalOpen * .38;
    approval.visible = p > .2;
    thresholds[0].rotation.y = -.09 + smooth(0, .28, p) * .09;
    thresholds[1].rotation.y = .11 - passingPolicy * .11;
    thresholds[2].rotation.y = -.12 + approvalOpen * .12;
    sculpture.rotation.y = pointerX * .12;
    sculpture.rotation.x = -pointerY * .035;

    camera.position.set(4.25 - p * 1.5 + pointerX * .24, 2.65 + p * .55 - pointerY * .16, 8.1 - p * .65);
    cameraTarget.set(0, .12, -.1 - p * .3);
    camera.lookAt(cameraTarget);
    renderer.render(scene, camera);
  }

  function dispose() {
    scene.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.geometry.dispose();
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach((material) => material.dispose());
      }
    });
    renderer.dispose();
    renderer.forceContextLoss();
  }

  return { resize, render, dispose };
}
