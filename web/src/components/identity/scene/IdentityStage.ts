// SPDX-License-Identifier: AGPL-3.0-or-later
// Ported from rhine-lock (https://github.com/tmc5csxr9m-cpu/rhine-lock) at a4df68c,
// frontend/src/lib/scene/Stage.ts. Modified 2026-11 for HpAgent: compact layout, cards gathering into the identity panel on
// confirmation, static single-frame rendering, stop/resume, graduated quality and fatal-load
// reporting. Look-dev URL parameters and standby were removed. See web/THIRD_PARTY_NOTICES.md.
import * as THREE from "three";
import { HDRLoader } from "three/examples/jsm/loaders/HDRLoader.js";
import {
  BlendFunction,
  DepthOfFieldEffect,
  Effect,
  EffectAttribute,
  EffectComposer,
  EffectPass,
  type Pass,
  RenderPass,
} from "postprocessing";
import { N8AOPostPass } from "n8ao";
import {
  cardCarveGlsl,
  cardUniformsGlsl,
  cardVertexGlsl,
  common,
  finCarveGlsl,
  finEmissiveGlsl,
  finSurfaceGlsl,
  finUniformsGlsl,
  finVertexGlsl,
  gradeFragment,
} from "./shaders";

export type StageQuality = "high" | "low";

const LOOK = { exposure: 1.5, key: 3.4, fill: 0.55, env: 0.8, sat: 0.7 };
const FIN_COUNT = 400;
const CARD_COUNT = 30;
const NARROW_CARD_COUNT = 14;
const DEG = Math.PI / 180;
const BASE_DISTANCE = 1080;
/** The sculpture's on-screen footprint, with a margin, in world units. */
const SCULPTURE_WIDTH = 780;
const STREAM_END_Z = 80;
const SLOW_FRAME_MS = 40;
const SLOW_FRAME_LIMIT = 45;

export interface StageTargets {
  verify: number;
  denied: number;
  parallaxX: number;
  parallaxY: number;
  cardsReveal: number;
  glitch: number;
  gather: number;
}

export interface StageLayout {
  /** The identity panel in viewport CSS pixels; the stream gathers into its left edge. */
  panel: { left: number; top: number; width: number; height: number } | null;
  /** No design canvas: the sculpture is fitted above a stacked panel. */
  compact: boolean;
}

class GradeEffect extends Effect {
  constructor() {
    super("IdentityGrade", gradeFragment, {
      blendFunction: BlendFunction.SET,
      attributes: EffectAttribute.CONVOLUTION,
      uniforms: new Map<string, THREE.Uniform>([
        ["uTime", new THREE.Uniform(0)],
        ["uGlitch", new THREE.Uniform(0)],
        ["uAberration", new THREE.Uniform(0.0016)],
        ["uVignette", new THREE.Uniform(0.32)],
        ["uDenied", new THREE.Uniform(0)],
        ["uExposure", new THREE.Uniform(1.0)],
        ["uCentre", new THREE.Uniform(new THREE.Vector2(0.45, 0.47))],
        ["uPaperBase", new THREE.Uniform(new THREE.Vector3(0.9, 0.9, 0.9))],
        ["uSat", new THREE.Uniform(LOOK.sat)],
      ]),
    });
  }
  set(name: string, value: number) {
    this.uniforms.get(name)!.value = value;
  }
}

// Khronos PBR Neutral, mirrored from the grade shader, and its inverse: picks a clear
// colour that lands exactly on the paper after tone mapping.
function neutral(c: number[]) {
  const start = 0.76;
  const desaturation = 0.15;
  const x = Math.min(...c);
  const offset = x < 0.08 ? x - 6.25 * x * x : 0.04;
  let v = c.map((k) => k - offset);
  const peak = Math.max(...v);
  if (peak < start) return v;
  const d = 1 - start;
  const newPeak = 1 - (d * d) / (peak + d - start);
  v = v.map((k) => (k * newPeak) / peak);
  const g = 1 - 1 / (desaturation * (peak - newPeak) + 1);
  return v.map((k) => k + (newPeak - k) * g);
}
function forward(c: number[]) {
  const t = neutral(c.map((x) => x * LOOK.exposure));
  const [r = 0, g = 0, b = 0] = t;
  const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return t.map((x) => l + (x - l) * LOOK.sat);
}
function inverseForward(target: number[]) {
  let x = target.slice();
  for (let i = 0; i < 40; ++i) {
    const y = forward(x);
    x = x.map((k, j) => Math.max(0, k + (target[j]! - y[j]!) / LOOK.exposure));
  }
  return x;
}
const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
const PAPER_LIGHT = [0.925, 0.918, 0.902];
const PAPER_DARK = [0.043, 0.043, 0.047];

function instanced(base: THREE.BufferGeometry, count: number) {
  const g = new THREE.InstancedBufferGeometry();
  g.index = base.index;
  g.setAttribute("position", base.getAttribute("position"));
  g.setAttribute("normal", base.getAttribute("normal"));
  g.setAttribute("uv", base.getAttribute("uv"));
  const index = new Float32Array(count);
  for (let i = 0; i < count; ++i) index[i] = i;
  g.setAttribute("aIndex", new THREE.InstancedBufferAttribute(index, 1));
  g.instanceCount = count;
  return g;
}

function finUniforms(pulseT: Float32Array, pulseO: Float32Array) {
  return {
    uTime: { value: 0 },
    uFlow: { value: 0 },
    uCount: { value: FIN_COUNT },
    uRadius: { value: 285 },
    uAspect: { value: 0.95 },
    uDepth: { value: 95 },
    uTwist: { value: 1.5 },
    uSpin: { value: 0 },
    uThickness: { value: 4.2 },
    uLean: { value: 0.62 },
    uCorner: { value: 0.14 },
    uWidth: { value: 84 },
    uHeight: { value: 84 },
    uAssemble: { value: 1 },
    uVerify: { value: 0 },
    uDenied: { value: 0 },
    uAlign: { value: 0 },
    uDisorder: { value: 0 },
    uDissolve: { value: 0 },
    uPulseT: { value: pulseT },
    uPulseO: { value: pulseO },
  };
}

function cardUniforms(cardTex: THREE.Texture) {
  return {
    uTime: { value: 0 },
    uFlow: { value: 0 },
    uCount: { value: CARD_COUNT },
    uWidth: { value: 104 },
    uThickness: { value: 3.2 },
    uReveal: { value: 0 },
    uDenied: { value: 0 },
    uCardTex: { value: cardTex },
    uGather: { value: 0 },
    uAnchor: { value: new THREE.Vector3(0, 0, STREAM_END_Z) },
  };
}

export class IdentityStage {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly sculpture = new THREE.Group();
  private readonly composer: EffectComposer;
  private readonly grade = new GradeEffect();
  private readonly finU: ReturnType<typeof finUniforms>;
  private readonly cardU: ReturnType<typeof cardUniforms>;
  private readonly cards: THREE.Mesh;
  private readonly key: THREE.DirectionalLight;
  private readonly textures: THREE.Texture[] = [];
  private readonly anchorRay = new THREE.Vector3();
  private readonly projected = new THREE.Vector3();
  private readonly anchorNdc = new THREE.Vector2();
  private dof: DepthOfFieldEffect | null = null;
  private heavyPasses: Pass[] = [];
  private environmentTarget: THREE.WebGLRenderTarget | null = null;
  private raf = 0;
  private disposed = false;
  private lastBase = -1;
  private lastFrame = 0;
  private lastRender = 0;
  private slowFrames = 0;
  private degradeLevel = 0;
  private painted = false;
  private width = 0;
  private height = 0;

  private time = 0;
  private flow = 0;
  private spin = 0;
  private cardFlow = 0;
  private disorder = 0;
  private dolly = 0;
  private assemble = 1;
  private assembleAnim: { from: number; t0: number; dur: number } | null = null;
  private dissolve = 0;
  private dissolveAnim: { t0: number; dur: number } | null = null;
  private lastPulseAt = -99;
  private readonly pulseT = new Float32Array(16).fill(-99);
  private readonly pulseO = new Float32Array(16).fill(0.25);
  private pulseSlot = 0;
  private distance = BASE_DISTANCE;
  private sculptureX = -10;
  private sculptureY = 0;
  private hasAnchor = false;

  /** Frame-rate cap; 0 renders every display frame. */
  maxFps = 0;
  targets: StageTargets = {
    verify: 0,
    denied: 0,
    parallaxX: 0,
    parallaxY: 0,
    cardsReveal: 0,
    glitch: 0,
    gather: 0,
  };
  onFrame: ((dt: number) => void) | null = null;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    quality: StageQuality,
    private readonly assetBase: string,
    private readonly onFatal: () => void,
  ) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,
      alpha: false,
      stencil: false,
      depth: true,
      premultipliedAlpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, quality === "low" ? 1 : 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.toneMapping = THREE.NoToneMapping;
    // The grade pass writes display-space values itself.
    this.renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    this.renderer.shadowMap.enabled = quality !== "low";
    this.renderer.shadowMap.type = THREE.PCFShadowMap;

    this.camera = new THREE.PerspectiveCamera(30, 16 / 9, 20, 6000);
    this.camera.rotation.order = "YXZ";

    this.key = this.lights();
    this.environment();

    // ── fins ──
    const finGeo = instanced(new THREE.BoxGeometry(1, 1, 1), FIN_COUNT);
    this.finU = finUniforms(this.pulseT, this.pulseO);
    const finMat = new THREE.MeshStandardMaterial({
      metalness: 0,
      roughness: 0.5,
      side: THREE.DoubleSide,
    });
    finMat.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, this.finU);
      sh.vertexShader = sh.vertexShader
        .replace(
          "#include <common>",
          `#include <common>\n${common}\n${finUniformsGlsl}\n${finVertexGlsl}`,
        )
        .replace(
          "#include <beginnormal_vertex>",
          `vec3 rlPos; vec3 rlNrm; rl_finTransform(position, normal, rlPos, rlNrm);\nvec3 objectNormal = rlNrm;`,
        )
        .replace("#include <begin_vertex>", "vec3 transformed = rlPos;");
      sh.fragmentShader = sh.fragmentShader
        .replace("#include <common>", `#include <common>\n${common}\n${finUniformsGlsl}`)
        .replace(
          "#include <clipping_planes_fragment>",
          `#include <clipping_planes_fragment>\n${finCarveGlsl}\n${finSurfaceGlsl}`,
        )
        .replace("#include <color_fragment>", "diffuseColor.rgb = rlBody;")
        .replace(
          "#include <roughnessmap_fragment>",
          "float roughnessFactor = mix(0.55, 0.36, bevel);",
        )
        .replace("#include <emissivemap_fragment>", finEmissiveGlsl);
    };
    const finDepth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
    finDepth.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, this.finU);
      sh.vertexShader = sh.vertexShader
        .replace(
          "#include <common>",
          `#include <common>\n${common}\n${finUniformsGlsl}\n${finVertexGlsl}`,
        )
        .replace(
          "#include <begin_vertex>",
          `vec3 rlPos; vec3 rlNrm; rl_finTransform(position, normal, rlPos, rlNrm);\nvec3 transformed = rlPos;`,
        );
      sh.fragmentShader = sh.fragmentShader
        .replace("#include <common>", `#include <common>\n${finUniformsGlsl}`)
        .replace(
          "#include <clipping_planes_fragment>",
          `#include <clipping_planes_fragment>\n${finCarveGlsl}`,
        );
    };
    const fins = new THREE.Mesh(finGeo, finMat);
    fins.customDepthMaterial = finDepth;
    fins.frustumCulled = false;
    fins.castShadow = fins.receiveShadow = true;
    this.sculpture.add(fins);
    this.scene.add(this.sculpture);

    // ── card stream ──
    const cardTex = new THREE.TextureLoader().load(
      `${assetBase}card.png`,
      () => this.repaintIfStatic(),
      undefined,
      () => this.fail(),
    );
    this.textures.push(cardTex);
    cardTex.colorSpace = THREE.SRGBColorSpace;
    cardTex.anisotropy = 8;
    this.cardU = cardUniforms(cardTex);
    const cardMat = new THREE.MeshStandardMaterial({
      metalness: 0,
      roughness: 0.42,
      side: THREE.DoubleSide,
    });
    cardMat.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, this.cardU);
      sh.vertexShader = sh.vertexShader
        .replace(
          "#include <common>",
          `#include <common>\n${common}\n${cardUniformsGlsl}\n${cardVertexGlsl}`,
        )
        .replace(
          "#include <beginnormal_vertex>",
          `vec3 rlPos; vec3 rlNrm; rl_cardTransform(position, normal, rlPos, rlNrm);\nvec3 objectNormal = rlNrm;`,
        )
        .replace("#include <begin_vertex>", "vec3 transformed = rlPos;");
      sh.fragmentShader = sh.fragmentShader
        .replace("#include <common>", `#include <common>\n${common}\n${cardUniformsGlsl}`)
        .replace(
          "#include <clipping_planes_fragment>",
          `#include <clipping_planes_fragment>\n${cardCarveGlsl}\nbool rlFace = abs(vLocal.z) > 0.49;`,
        )
        .replace(
          "#include <color_fragment>",
          `{
          vec3 c = texture2D(uCardTex, rlFace ? vCardUv : vec2(0.5)).rgb;
          vec3 graphite = rl_toLinear(vec3(0.07, 0.072, 0.078));
          diffuseColor.rgb = mix(c, graphite + (c - rl_toLinear(vec3(0.94))) * 0.25, uDenied);
        }`,
        )
        .replace(
          "#include <roughnessmap_fragment>",
          "float roughnessFactor = rlFace ? 0.42 : 0.25;",
        )
        .replace(
          "#include <emissivemap_fragment>",
          "totalEmissiveRadiance = (rlFace ? 0.0 : 1.0) * mix(vec3(0.06), rl_toLinear(vec3(1.0, 0.48, 0.0)) * 0.9, uDenied);",
        );
    };
    const cardDepth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
    cardDepth.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, this.cardU);
      sh.vertexShader = sh.vertexShader
        .replace(
          "#include <common>",
          `#include <common>\n${common}\n${cardUniformsGlsl}\n${cardVertexGlsl}`,
        )
        .replace(
          "#include <begin_vertex>",
          `vec3 rlPos; vec3 rlNrm; rl_cardTransform(position, normal, rlPos, rlNrm);\nvec3 transformed = rlPos;`,
        );
      sh.fragmentShader = sh.fragmentShader
        .replace("#include <common>", `#include <common>\n${cardUniformsGlsl}`)
        .replace(
          "#include <clipping_planes_fragment>",
          `#include <clipping_planes_fragment>\n${cardCarveGlsl}`,
        );
    };
    this.cards = new THREE.Mesh(instanced(new THREE.BoxGeometry(1, 1, 1), CARD_COUNT), cardMat);
    this.cards.customDepthMaterial = cardDepth;
    this.cards.frustumCulled = false;
    this.cards.castShadow = this.cards.receiveShadow = true;
    this.scene.add(this.cards);

    // ── post ──
    this.composer = new EffectComposer(this.renderer, {
      frameBufferType: THREE.HalfFloatType,
      multisampling: quality === "high" ? 4 : 0,
    });
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    if (quality === "high") {
      const ao = new N8AOPostPass(this.scene, this.camera, 1920, 1080);
      ao.configuration.aoRadius = 26;
      ao.configuration.distanceFalloff = 1.2;
      ao.configuration.intensity = 2.2;
      ao.configuration.gammaCorrection = false;
      ao.configuration.screenSpaceRadius = false;
      ao.setQualityMode("Medium");
      this.composer.addPass(ao);
      this.dof = new DepthOfFieldEffect(this.camera, {
        focusDistance: 1060,
        focusRange: 520,
        bokehScale: 1.2,
        resolutionScale: 0.5,
      });
      const dofPass = new EffectPass(this.camera, this.dof);
      this.composer.addPass(dofPass);
      this.heavyPasses = [ao, dofPass];
    }
    this.composer.addPass(new EffectPass(this.camera, this.grade));

    canvas.addEventListener("webglcontextlost", this.contextLost);
    this.setLayout({ panel: null, compact: false });
  }

  private lights() {
    const key = new THREE.DirectionalLight(0xfff6ea, LOOK.key);
    key.position.set(-0.379, 0.788, 0.485).multiplyScalar(1200);
    key.castShadow = true;
    key.shadow.mapSize.set(4096, 4096);
    const cam = key.shadow.camera as THREE.OrthographicCamera;
    cam.left = -520;
    cam.right = 520;
    cam.top = 420;
    cam.bottom = -420;
    cam.near = 400;
    cam.far = 2000;
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.6;
    key.shadow.radius = 5;
    this.scene.add(key, key.target);
    const fill = new THREE.DirectionalLight(0xdfe8ff, LOOK.fill);
    fill.position.set(0.853, 0.174, -0.4925).multiplyScalar(1200);
    this.scene.add(fill, fill.target);
    return key;
  }

  private environment() {
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    new HDRLoader().load(
      `${this.assetBase}studio.hdr`,
      (hdr) => {
        if (this.disposed) {
          hdr.dispose();
          pmrem.dispose();
          return;
        }
        hdr.mapping = THREE.EquirectangularReflectionMapping;
        this.environmentTarget = pmrem.fromEquirectangular(hdr);
        this.scene.environment = this.environmentTarget.texture;
        this.scene.environmentIntensity = LOOK.env;
        this.scene.environmentRotation.set(0, -25 * DEG, 0);
        hdr.dispose();
        pmrem.dispose();
        this.repaintIfStatic();
      },
      undefined,
      () => {
        pmrem.dispose();
        this.fail();
      },
    );
  }

  private readonly contextLost = (event: Event) => {
    event.preventDefault();
    this.fail();
  };

  private fail() {
    if (this.disposed) return;
    this.stop();
    this.onFatal();
  }

  /** The design-canvas composition is fixed as upstream; only compact layouts refit. */
  setLayout({ panel, compact }: StageLayout) {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    if (w !== this.width || h !== this.height) {
      this.width = w;
      this.height = h;
      this.applySize();
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }

    if (compact) {
      const unitHeight = 2 * Math.tan((this.camera.fov / 2) * DEG);
      this.distance = Math.max(BASE_DISTANCE, SCULPTURE_WIDTH / (unitHeight * this.camera.aspect));
      this.sculptureX = 0;
      this.sculptureY = unitHeight * this.distance * 0.2;
    } else {
      this.distance = BASE_DISTANCE;
      this.sculptureX = -10;
      this.sculptureY = 0;
    }
    if (this.dof) this.dof.cocMaterial.focusDistance = this.distance - 20;

    this.hasAnchor = !!panel && !compact;
    if (panel) {
      this.anchorNdc.set((panel.left / w) * 2 - 1, -((panel.top + panel.height / 2) / h) * 2 + 1);
    }
    const count = compact ? NARROW_CARD_COUNT : CARD_COUNT;
    (this.cards.geometry as THREE.InstancedBufferGeometry).instanceCount = count;
    this.cardU.uCount.value = count;
    this.cardFlow %= count;
  }

  /** Projects a point on the band (curve parameter 0..1) to viewport CSS pixels. */
  project(u: number) {
    const t = u * Math.PI * 2;
    const { uRadius, uAspect, uDepth } = this.finU;
    const point = this.projected.set(
      uRadius.value * Math.cos(t),
      uRadius.value * uAspect.value * Math.sin(t) * Math.cos(t),
      uDepth.value * Math.sin(t),
    );
    this.sculpture.localToWorld(point);
    point.project(this.camera);
    return { x: (point.x * 0.5 + 0.5) * this.width, y: (-point.y * 0.5 + 0.5) * this.height };
  }

  pulse(origin = 0.25) {
    const i = this.pulseSlot;
    this.pulseT[i] = this.time;
    this.pulseO[i] = origin;
    this.pulseSlot = (i + 1) % 16;
    this.lastPulseAt = this.time;
  }
  playAssemble(duration = 2300) {
    this.assemble = 0;
    this.assembleAnim = { from: 0, t0: this.time, dur: duration / 1000 };
  }
  setAssembled() {
    this.assemble = 1;
    this.assembleAnim = null;
  }
  playDissolve(duration = 1100) {
    this.dissolveAnim = { t0: this.time, dur: duration / 1000 };
  }
  /** Whether the scene is animating something beyond its ambient drift. */
  get busy() {
    return !!this.assembleAnim || !!this.dissolveAnim || this.time - this.lastPulseAt < 2.6;
  }

  start() {
    if (this.disposed || this.raf) return;
    this.lastFrame = performance.now();
    const loop = (now: number) => {
      if (this.disposed) return;
      this.raf = requestAnimationFrame(loop);
      if (this.maxFps > 0 && now - this.lastRender < 1000 / this.maxFps - 2) return;
      this.lastRender = now;
      const raw = now - this.lastFrame;
      this.lastFrame = now;
      this.watchFrameCost(raw);
      this.frame(Math.min(raw / 1000, 0.05));
    };
    this.raf = requestAnimationFrame(loop);
  }

  stop() {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  /** Draws the present state once, without any animation owner. */
  renderOnce() {
    if (this.disposed) return;
    this.painted = true;
    this.frame(0);
  }

  /** A static frame drawn before an asset arrived must be redrawn with it. */
  private repaintIfStatic() {
    if (this.painted && !this.raf) this.renderOnce();
  }

  private watchFrameCost(ms: number) {
    // A capped frame is long by design; only measure against the active budget.
    const budget = this.maxFps > 0 ? 1000 / this.maxFps + SLOW_FRAME_MS / 2 : SLOW_FRAME_MS;
    this.slowFrames = ms > budget ? this.slowFrames + 1 : Math.max(0, this.slowFrames - 1);
    if (this.slowFrames < SLOW_FRAME_LIMIT) return;
    this.slowFrames = 0;
    this.degrade();
  }

  private degrade() {
    this.degradeLevel += 1;
    if (this.degradeLevel === 1) {
      this.renderer.setPixelRatio(1);
      this.applySize();
    } else if (this.degradeLevel === 2) {
      for (const pass of this.heavyPasses) this.composer.removePass(pass);
      this.heavyPasses = [];
      this.dof = null;
      this.key.castShadow = false;
    }
  }

  private applySize() {
    this.renderer.setSize(this.width, this.height, false);
    this.composer.setSize(this.width, this.height, false);
  }

  private frame(dt: number) {
    this.time += dt;
    this.onFrame?.(dt);
    const T = this.targets;

    // Conveyor stays below one card spacing per frame (no wagon-wheel strobing).
    this.flow = (this.flow + dt * (1.6 + T.verify * 4.0)) % FIN_COUNT;
    this.spin += dt * (0.05 + T.verify * 1.4);
    this.cardFlow = (this.cardFlow + dt * (0.55 + T.verify * 0.9)) % this.cardU.uCount.value;
    this.disorder += (T.denied * 0.12 - this.disorder) * (1 - Math.exp(-dt * 3.2));
    this.dolly += (T.verify * 70 - this.dolly) * (1 - Math.exp(-dt * 2.0));

    if (this.assembleAnim) {
      const a = this.assembleAnim;
      const x = Math.min(1, (this.time - a.t0) / a.dur);
      this.assemble = a.from + (1 - a.from) * (0.5 - 0.5 * Math.cos(Math.PI * x));
      if (x >= 1) this.assembleAnim = null;
    }
    if (this.dissolveAnim) {
      const x = Math.min(1, (this.time - this.dissolveAnim.t0) / this.dissolveAnim.dur);
      this.dissolve = x * x;
    }

    const intro = 1 - Math.pow(1 - Math.min(1, Math.max(0, this.assemble)), 3);
    const u = this.finU;
    u.uTime.value = this.time;
    u.uFlow.value = this.flow;
    u.uSpin.value = this.spin;
    u.uAssemble.value = this.assemble;
    u.uVerify.value = T.verify;
    u.uDenied.value = T.denied;
    u.uAlign.value = T.verify * 0.5;
    u.uDisorder.value = this.disorder;
    u.uDissolve.value = this.dissolve;

    this.camera.position.set(
      T.parallaxX * 40,
      60 + T.parallaxY * 24,
      this.distance + (1 - intro) * 300 - this.dolly,
    );
    this.camera.rotation.set((-3 - T.parallaxY * 1.2) * DEG, T.parallaxX * 1.6 * DEG, 0);
    this.camera.updateMatrixWorld();
    this.sculpture.position.set(this.sculptureX, this.sculptureY, 0);
    this.sculpture.rotation.order = "YXZ";
    this.sculpture.rotation.set(
      (16 + Math.sin(this.time * 0.11) * 3) * DEG,
      (Math.sin(this.time * 0.07) * 9 - (1 - intro) * 32) * DEG,
      -2 * DEG,
    );

    const c = this.cardU;
    c.uTime.value = this.time;
    c.uFlow.value = this.cardFlow;
    c.uReveal.value = T.cardsReveal * (1 - this.dissolve);
    c.uDenied.value = T.denied;
    c.uGather.value = this.hasAnchor ? T.gather : 0;
    if (this.hasAnchor) this.updateAnchor(c.uAnchor.value);

    this.scene.environmentIntensity = LOOK.env * (1 - T.denied * 0.72);
    this.grade.set("uExposure", LOOK.exposure);
    const base = PAPER_LIGHT.map((l, i) => l + (PAPER_DARK[i]! - l) * T.denied);
    if (Math.abs(base[0]! - this.lastBase) > 1e-4) {
      this.lastBase = base[0]!;
      const clear = inverseForward(base.map(srgbToLinear));
      this.renderer.setClearColor(
        new THREE.Color().setRGB(clear[0]!, clear[1]!, clear[2]!, THREE.LinearSRGBColorSpace),
        0,
      );
      (this.grade.uniforms.get("uPaperBase")!.value as THREE.Vector3).set(
        base[0]!,
        base[1]!,
        base[2]!,
      );
    }
    this.grade.set("uTime", this.time);
    this.grade.set("uGlitch", T.glitch);
    this.grade.set("uDenied", T.denied);
    this.grade.set("uVignette", 0.32 + 0.25 * T.denied);

    this.composer.render(dt);
  }

  /** Unprojects the panel edge onto the plane where the stream ends. */
  private updateAnchor(out: THREE.Vector3) {
    const origin = this.camera.position;
    const ray = this.anchorRay
      .set(this.anchorNdc.x, this.anchorNdc.y, 0.5)
      .unproject(this.camera)
      .sub(origin)
      .normalize();
    out.copy(origin).addScaledVector(ray, (STREAM_END_Z - origin.z) / ray.z);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.stop();
    this.canvas.removeEventListener("webglcontextlost", this.contextLost);
    const materials = new Set<THREE.Material>();
    this.scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.geometry.dispose();
      for (const material of [mesh.material, mesh.customDepthMaterial].flat())
        if (material) materials.add(material);
    });
    for (const material of materials) material.dispose();
    for (const texture of this.textures) texture.dispose();
    this.environmentTarget?.dispose();
    this.environmentTarget = null;
    this.scene.environment = null;
    this.composer.dispose();
    this.renderer.dispose();
  }
}
