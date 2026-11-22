"use client";

// WebGL voice orb (three.js + custom shaders). Loaded lazily (next/dynamic) so three.js never blocks first paint.
// Reacts to a Signal read every frame: breathes when idle, pulses with agent speech, ripples with caller speech,
// shatters into shards on barge-in and reassembles.
import { useEffect, useRef } from "react";
import {
  AdditiveBlending,
  BufferAttribute,
  Color,
  IcosahedronGeometry,
  Mesh,
  NormalBlending,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  ShaderMaterial,
  WebGLRenderer,
} from "three";
import type { OrbSignal as Signal, OrbState as VState } from "./types";

type Palette = [string, string, string];
const DARK: Record<VState, Palette> = {
  IDLE: ["#14b8a6", "#2563eb", "#a5f3fc"],
  LISTENING: ["#14c8b4", "#2f6bff", "#a5f3fc"],
  USER_SPEAKING: ["#38bdf8", "#dbeafe", "#ffffff"],
  PROCESSING: ["#6366f1", "#22d3ee", "#c7d2fe"],
  AGENT_SPEAKING: ["#ff4f7b", "#ffb347", "#ffe1ec"],
  INTERRUPTED: ["#ff2d55", "#ffffff", "#ff9aa2"],
  TRANSFER_REQUESTED: ["#6366f1", "#a78bfa", "#ddd6fe"],
  ENDED: ["#5b6b82", "#8aa0b8", "#dbe4ee"],
  ERROR: ["#9f1239", "#4c0519", "#fda4af"],
};
const LIGHT: Record<VState, Palette> = {
  IDLE: ["#06b6d4", "#6366f1", "#cffafe"],
  LISTENING: ["#14b8a6", "#3b82f6", "#cffafe"],
  USER_SPEAKING: ["#0284c7", "#7dd3fc", "#e0f2fe"],
  PROCESSING: ["#4f46e5", "#06b6d4", "#a5b4fc"],
  AGENT_SPEAKING: ["#e11d63", "#f59e0b", "#fecdd3"],
  INTERRUPTED: ["#e11d48", "#fda4af", "#ffe4e6"],
  TRANSFER_REQUESTED: ["#4f46e5", "#8b5cf6", "#ddd6fe"],
  ENDED: ["#64748b", "#94a3b8", "#cbd5e1"],
  ERROR: ["#be123c", "#881337", "#fecdd3"],
};

// Ashima Arts 3D simplex noise (MIT).
const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const ORB_VERT = /* glsl */ `
uniform float uTime, uAmp, uRipple, uBreak, uBreathe, uThink;
attribute vec3 aCenter;
attribute float aRand;
varying vec3 vN;
varying vec3 vV;
varying float vNoise;
varying float vShard;
${NOISE}
vec3 rot(vec3 v, vec3 k, float a){ float c=cos(a), s=sin(a); return v*c + cross(k,v)*s + k*dot(k,v)*(1.0-c); }
float dispAt(vec3 n){
  float coarse = snoise(n*1.5 + vec3(0.0, uTime*0.22, uTime*0.13));
  float fine = snoise(n*3.6 - vec3(uTime*0.55));
  float d = uBreathe + coarse*(0.05 + uAmp*0.21 + uThink*0.06) + fine*uAmp*0.07;
  // caller ripple: concentric waves travelling across the surface from the side facing the viewer
  float ang = acos(clamp(dot(n, normalize(vec3(0.25, 0.3, 1.0))), -1.0, 1.0));
  d += uRipple * 0.085 * sin(ang*13.0 - uTime*10.0) * exp(-ang*0.45);
  return d;
}
void main(){
  vec3 n = normalize(position);
  vec3 p = n * (1.0 + dispAt(n));
  // barge-in shatter: every triangle flies out along its centre normal, spins, then reforms as uBreak -> 0
  vec3 cN = normalize(aCenter);
  vec3 c = cN * (1.0 + dispAt(cN));
  float b = uBreak * (0.45 + aRand*1.1);
  vec3 axis = normalize(cross(cN, vec3(aRand, 1.0-aRand, 0.37)) + 1e-4);
  vec3 local = rot(p - c, axis, b*(aRand*7.0-3.5)) * (1.0 - uBreak*0.25);
  p = c + cN*b*0.85 + local;
  vNoise = snoise(n*1.5 + vec3(0.0, uTime*0.22, uTime*0.13));
  vShard = uBreak * (0.4 + aRand*0.6);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vN = normalize(normalMatrix * n);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

const ORB_FRAG = /* glsl */ `
uniform vec3 uA, uB, uC;
uniform float uTime, uAmp, uLight;
varying vec3 vN;
varying vec3 vV;
varying float vNoise;
varying float vShard;
void main(){
  float fres = pow(1.0 - max(dot(normalize(vN), normalize(vV)), 0.0), 2.0);
  float band = 0.5 + 0.5*sin(vNoise*3.2 + uTime*0.7);
  vec3 col = mix(uA, uB, smoothstep(-0.55, 0.85, vNoise));
  col = mix(col, uC, fres*0.7);
  // dark: deep body + bright rim (reads as glow); light: luminous body, softer rim (reads as glass)
  float shadeDark = 0.62 + band*0.22 + fres*0.55;
  float shadeLight = 0.98 + band*0.16 + fres*0.2;
  col *= mix(shadeDark, shadeLight, uLight);
  col += uC * uAmp * 0.22;
  col += vec3(1.0, 0.86, 0.88) * vShard * 0.9;
  col = mix(col, vec3(1.0), uLight * 0.08); // lift the orb on light backgrounds
  gl_FragColor = vec4(col, 1.0);
}`;

const HALO_VERT = /* glsl */ `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const HALO_FRAG = /* glsl */ `
uniform vec3 uA, uC;
uniform float uTime, uAmp, uRipple, uBreak, uLight;
varying vec2 vUv;
void main(){
  float r = length(vUv - 0.5) * 2.0;
  float glow = smoothstep(1.0, 0.18, r) * (0.42 + uAmp*0.55 + uBreak*0.6);
  float rings = uRipple * smoothstep(1.0, 0.35, r) * smoothstep(0.3, 0.42, r) * (0.5 + 0.5*sin(r*34.0 - uTime*9.0)) * 0.5;
  vec3 col = mix(uA, uC, 0.35) ;
  float a = (glow*glow + rings) * (1.0 - uLight*0.55);
  gl_FragColor = vec4(col * (1.0 + uBreak), a);
}`;

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const easeInOut = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

/** Break envelope after a barge-in starts: shatter fast, hold, reassemble. */
function breakEnvelope(dt: number) {
  if (dt < 0) return 0;
  if (dt < 160) return dt / 160;
  if (dt < 520) return 1;
  if (dt < 1800) return 1 - easeInOut((dt - 520) / 1280);
  return 0;
}

// Probe once per page: three.js logs console errors when it can't get a context, so we check quietly first.
let webglOk: boolean | null = null;
function hasWebGL() {
  if (webglOk !== null) return webglOk;
  try {
    const gl = document.createElement("canvas").getContext("webgl2") ?? document.createElement("canvas").getContext("webgl");
    webglOk = !!gl;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    webglOk = false;
  }
  return webglOk;
}

export default function Orb({
  signal,
  light,
  reduced,
  onReady,
}: {
  signal: React.RefObject<() => Signal>;
  light: boolean;
  reduced: boolean;
  onReady?: (ok: boolean) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const lightRef = useRef(light);
  const reducedRef = useRef(reduced);
  useEffect(() => {
    lightRef.current = light;
    reducedRef.current = reduced;
  }, [light, reduced]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    if (!hasWebGL()) {
      onReady?.(false);
      return;
    }
    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch (e) {
      console.warn("[orb] WebGL unavailable, using CSS fallback", e);
      onReady?.(false);
      return;
    }
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setClearColor(0x000000, 0);
    el.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");

    const scene = new Scene();
    const camera = new PerspectiveCamera(34, 1, 0.1, 50);
    camera.position.set(0, 0, 8.7); // canvas is 1.6× the orb box (see host inset) so shards never hit its edge

    const geo = new IcosahedronGeometry(1, 26); // non-indexed: every triangle owns its vertices → can shatter
    const pos = geo.getAttribute("position");
    const centers = new Float32Array(pos.count * 3);
    const rands = new Float32Array(pos.count);
    for (let i = 0; i < pos.count; i += 3) {
      const cxv = (pos.getX(i) + pos.getX(i + 1) + pos.getX(i + 2)) / 3;
      const cyv = (pos.getY(i) + pos.getY(i + 1) + pos.getY(i + 2)) / 3;
      const czv = (pos.getZ(i) + pos.getZ(i + 1) + pos.getZ(i + 2)) / 3;
      const r = Math.abs(Math.sin(i * 12.9898 + 78.233) * 43758.5453) % 1;
      for (let k = 0; k < 3; k++) {
        centers.set([cxv, cyv, czv], (i + k) * 3);
        rands[i + k] = r;
      }
    }
    geo.setAttribute("aCenter", new BufferAttribute(centers, 3));
    geo.setAttribute("aRand", new BufferAttribute(rands, 1));

    const pal0 = (lightRef.current ? LIGHT : DARK).LISTENING;
    const cA = new Color(pal0[0]);
    const cB = new Color(pal0[1]);
    const cC = new Color(pal0[2]);
    const shared = { uTime: { value: 0 }, uAmp: { value: 0 }, uRipple: { value: 0 }, uBreak: { value: 0 }, uLight: { value: 0 } };
    const orbMat = new ShaderMaterial({
      vertexShader: ORB_VERT,
      fragmentShader: ORB_FRAG,
      uniforms: { ...shared, uBreathe: { value: 0 }, uThink: { value: 0 }, uA: { value: cA }, uB: { value: cB }, uC: { value: cC } },
    });
    const orb = new Mesh(geo, orbMat);
    const haloMat = new ShaderMaterial({
      vertexShader: HALO_VERT,
      fragmentShader: HALO_FRAG,
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
      uniforms: { ...shared, uA: { value: cA }, uC: { value: cC } },
    });
    const halo = new Mesh(new PlaneGeometry(5.6, 5.6), haloMat);
    halo.position.z = -1.2;
    scene.add(halo, orb);

    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    let visible = true;
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting), { threshold: 0 });
    io.observe(el);

    let raf = 0;
    let last = performance.now();
    let clock = 0;
    let prevState: VState = "LISTENING";
    let breakStart = -1e9;
    const tA = new Color();
    const tB = new Color();
    const tC = new Color();
    let readySent = false;

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(64, now - last);
      last = now;
      if (!visible) return;
      const red = reducedRef.current;
      const lightNow = lightRef.current;
      const raw = signal.current?.() ?? { state: "LISTENING", amp: 0 };
      const sig = { state: (raw.state in DARK ? raw.state : "IDLE") as VState, amp: Math.max(0, Math.min(1, raw.amp || 0)) };
      if (sig.state === "INTERRUPTED" && prevState !== "INTERRUPTED") breakStart = now;
      prevState = sig.state;
      if (!red) clock += dt / 1000;

      const k = red ? 1 : 1 - Math.pow(0.0009, dt / 1000); // frame-rate independent smoothing
      const kc = red ? 1 : 1 - Math.pow(0.02, dt / 1000);
      const speakingAgent = sig.state === "AGENT_SPEAKING";
      const speakingCaller = sig.state === "USER_SPEAKING";
      const u = orbMat.uniforms;
      u.uTime.value = red ? 1.7 : clock;
      u.uAmp.value = lerp(u.uAmp.value, speakingAgent ? 0.35 + sig.amp * 0.75 : speakingCaller ? 0.18 + sig.amp * 0.2 : 0, k);
      u.uRipple.value = lerp(u.uRipple.value, speakingCaller ? 0.55 + sig.amp * 0.55 : 0, k);
      u.uThink.value = lerp(u.uThink.value, sig.state === "PROCESSING" || sig.state === "TRANSFER_REQUESTED" ? 1 : 0, k);
      u.uBreathe.value = red ? 0 : (sig.state === "ENDED" || sig.state === "ERROR" ? 0.01 : 0.032) * Math.sin(clock * 1.25);
      u.uBreak.value = red ? (sig.state === "INTERRUPTED" ? 0.55 : 0) : breakEnvelope(now - breakStart);
      u.uLight.value = lightNow ? 1 : 0;
      haloMat.blending = lightNow ? NormalBlending : AdditiveBlending;

      const pal = (lightNow ? LIGHT : DARK)[sig.state];
      tA.set(pal[0]);
      tB.set(pal[1]);
      tC.set(pal[2]);
      cA.lerp(tA, kc);
      cB.lerp(tB, kc);
      cC.lerp(tC, kc);

      if (!red) {
        orb.rotation.y += dt * (0.00018 + u.uAmp.value * 0.0005);
        orb.rotation.x = Math.sin(clock * 0.21) * 0.18;
      }
      const s = 1 + (speakingAgent ? sig.amp * 0.05 : 0);
      orb.scale.setScalar(lerp(orb.scale.x, s, k));
      renderer.render(scene, camera);
      if (!readySent) {
        readySent = true;
        onReady?.(true);
      }
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      geo.dispose();
      orbMat.dispose();
      haloMat.dispose();
      halo.geometry.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [signal, onReady]);

  return <div ref={host} style={{ position: "absolute", inset: "-30%", pointerEvents: "none" }} />;
}
