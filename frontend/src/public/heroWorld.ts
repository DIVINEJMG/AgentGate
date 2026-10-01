import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export type HeroWorld = {
  resize: (width: number, height: number) => void;
  render: (slide: number, pointerX: number, pointerY: number, time: number, animate: boolean) => void;
  dispose: () => void;
};

const material = (color: number, metalness: number, roughness: number) =>
  new THREE.MeshPhysicalMaterial({ color, metalness, roughness, clearcoat: .55, clearcoatRoughness: .22 });

export function createHeroWorld(canvas: HTMLCanvasElement): HeroWorld {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.35));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.65;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, .1, 60);
  const anchor = new THREE.Group();
  scene.add(anchor);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x72877b, 2.6));
  const key = new THREE.DirectionalLight(0xffffff, 5);
  key.position.set(-4, 6, 8);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xa9d5bf, 4);
  rim.position.set(5, 2, -6);
  scene.add(rim);
  const warm = new THREE.PointLight(0xece0b7, 55, 13);
  warm.position.set(2, -2, 4);
  scene.add(warm);

  const graphite = material(0x26382f, .73, .29);
  const silver = material(0xd5ded4, .78, .23);
  const pearl = material(0xf0f2e8, .22, .36);
  const jade = material(0x4a8061, .47, .24);
  const brass = material(0xb1a971, .66, .28);
  const glow = new THREE.MeshStandardMaterial({ color: 0x9bc8a2, emissive: 0x477e5b, emissiveIntensity: 1.1, roughness: .3 });

  const models = [new THREE.Group(), new THREE.Group(), new THREE.Group()];
  models.forEach((model) => anchor.add(model));

  // An active work unit is surrounded by precise orbital paths and small queued modules.
  const work = models[0];
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(.83, 3), jade);
  core.rotation.set(.18, .3, -.1);
  work.add(core);
  const coreShellMaterial = material(0xd5ded4, .65, .28);
  coreShellMaterial.wireframe = true;
  coreShellMaterial.transparent = true;
  coreShellMaterial.opacity = .32;
  const coreShell = new THREE.Mesh(new THREE.IcosahedronGeometry(.9, 1), coreShellMaterial);
  work.add(coreShell);
  for (let index = 0; index < 3; index += 1) {
    const orbit = new THREE.Mesh(new THREE.TorusGeometry(1.37 + index * .17, index === 1 ? .036 : .022, 12, 120), index === 1 ? brass : graphite);
    orbit.rotation.set(.38 + index * .6, -.42 + index * .8, index * .7);
    work.add(orbit);
  }
  const workSatellites = new THREE.Group();
  for (let index = 0; index < 6; index += 1) {
    const angle = index * Math.PI / 3;
    const module = new THREE.Group();
    const slab = new THREE.Mesh(new RoundedBoxGeometry(.38, .28, .12, 3, .045), index % 2 ? silver : graphite);
    module.add(slab);
    const inlay = new THREE.Mesh(new RoundedBoxGeometry(.22, .025, .015, 2, .008), glow);
    inlay.position.z = .07;
    module.add(inlay);
    module.position.set(Math.cos(angle) * 1.75, Math.sin(angle) * 1.5, Math.sin(angle * 2) * .48);
    module.rotation.set(angle * .24, angle * .5, angle * .12);
    workSatellites.add(module);
  }
  work.add(workSatellites);

  // The policy scene reads as an engineered aperture rather than a decorative ring.
  const policy = models[1];
  const outer = new THREE.Mesh(new THREE.TorusGeometry(1.7, .16, 16, 96), graphite);
  policy.add(outer);
  const inner = new THREE.Mesh(new THREE.TorusGeometry(1.38, .035, 10, 96), brass);
  policy.add(inner);
  const policyDisk = new THREE.Mesh(new THREE.CylinderGeometry(.72, .72, .17, 64), jade);
  policyDisk.rotation.x = Math.PI / 2;
  policy.add(policyDisk);
  const policyFacet = new THREE.Mesh(new THREE.IcosahedronGeometry(.54, 1), pearl);
  policyFacet.position.z = .22;
  policy.add(policyFacet);
  const shutters = new THREE.Group();
  for (let index = 0; index < 8; index += 1) {
    const angle = index * Math.PI / 4;
    const blade = new THREE.Mesh(new RoundedBoxGeometry(.95, .31, .16, 3, .045), index % 2 ? silver : graphite);
    blade.position.set(Math.cos(angle) * 1.03, Math.sin(angle) * 1.03, .12 + index * .008);
    blade.rotation.z = angle + Math.PI / 2;
    shutters.add(blade);
    const pin = new THREE.Mesh(new THREE.SphereGeometry(.06, 12, 8), brass);
    pin.position.set(Math.cos(angle) * 1.69, Math.sin(angle) * 1.69, .18);
    policy.add(pin);
  }
  policy.add(shutters);
  const policyArc = new THREE.Mesh(new THREE.TorusGeometry(2.01, .012, 8, 90, Math.PI * 1.45), jade);
  policyArc.rotation.z = -.5;
  policy.add(policyArc);

  // A held decision opens into a visible approval point between two structural leaves.
  const oversight = models[2];
  const approvalCore = new THREE.Mesh(new THREE.OctahedronGeometry(.61, 2), glow);
  approvalCore.position.z = .22;
  oversight.add(approvalCore);
  const approvalFrame = new THREE.Mesh(new THREE.TorusGeometry(.96, .035, 10, 96), brass);
  approvalFrame.position.z = .08;
  oversight.add(approvalFrame);
  const leaves: THREE.Group[] = [];
  for (const side of [-1, 1]) {
    const leaf = new THREE.Group();
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(side * .22, -1.32, .05),
      new THREE.Vector3(side * 1.32, -.95, .14),
      new THREE.Vector3(side * 1.48, 0, -.05),
      new THREE.Vector3(side * 1.28, .98, .14),
      new THREE.Vector3(side * .18, 1.35, .05),
    ]);
    leaf.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 48, .18, 12, false), side === -1 ? graphite : silver));
    const spine = new THREE.Mesh(new RoundedBoxGeometry(.12, 2.3, .12, 3, .035), side === -1 ? jade : brass);
    spine.position.set(side * 1.64, 0, -.28);
    leaf.add(spine);
    for (let index = 0; index < 3; index += 1) {
      const connector = new THREE.Mesh(new RoundedBoxGeometry(.45, .055, .06, 2, .02), silver);
      connector.position.set(side * 1.45, -.7 + index * .7, -.18);
      connector.rotation.z = side * .15;
      leaf.add(connector);
    }
    oversight.add(leaf);
    leaves.push(leaf);
  }
  const halo = new THREE.Mesh(new THREE.TorusGeometry(1.82, .018, 8, 120), jade);
  halo.rotation.set(.35, -.16, .1);
  oversight.add(halo);

  let mobile = false;
  let compactMobile = false;
  function resize(width: number, height: number) {
    if (!width || !height) return;
    mobile = width < 700;
    compactMobile = mobile && height < 740;
    camera.aspect = width / height;
    camera.fov = mobile ? 39 : 35;
    camera.position.set(0, 0, mobile ? 11.1 : 8.6);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    anchor.position.set(mobile ? 0 : 3.2, mobile ? (compactMobile ? .55 : .45) : -.55, 0);
    renderer.setSize(width, height, false);
  }

  function render(slide: number, pointerX: number, pointerY: number, time: number, animate: boolean) {
    models.forEach((model, index) => {
      const distance = index - slide;
      const presence = Math.max(0, 1 - Math.abs(distance) * 2);
      model.visible = presence > .02;
      model.position.x = distance * 1.2;
      model.scale.setScalar((.15 + .85 * presence) * (mobile ? (compactMobile ? .62 : .78) : .68));
      model.rotation.y = pointerX * .2 + (animate ? Math.sin(time * .25 + index) * .075 : 0);
      model.rotation.x = -pointerY * .12 + (animate ? Math.sin(time * .37 + index) * .04 : 0);
      model.position.y = animate ? Math.sin(time * .75 + index) * .075 : 0;
    });
    workSatellites.rotation.z = animate ? time * .08 : 0;
    core.rotation.y = animate ? time * .13 : .3;
    shutters.rotation.z = animate ? Math.sin(time * .45) * .09 : 0;
    policyFacet.rotation.y = animate ? time * .18 : 0;
    leaves[0].position.x = animate ? -.12 + Math.sin(time * .6) * .025 : -.12;
    leaves[1].position.x = animate ? .12 - Math.sin(time * .6) * .025 : .12;
    approvalCore.rotation.y = animate ? time * .2 : 0;
    renderer.render(scene, camera);
  }

  function dispose() {
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      geometries.add(object.geometry);
      (Array.isArray(object.material) ? object.material : [object.material]).forEach((item) => materials.add(item));
    });
    geometries.forEach((item) => item.dispose());
    materials.forEach((item) => item.dispose());
    renderer.dispose();
    renderer.forceContextLoss();
  }

  return { resize, render, dispose };
}
