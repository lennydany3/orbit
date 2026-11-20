// The Orbit particle sphere. Each point is a small quad (an instance of six vertices) placed by the
// vertex stage from its fixed direction and four random numbers; everything that makes the sphere
// look alive (noise, ripples, sparks, scan band, amber pulse, rim arc) is a uniform blended on the CPU.
// Output is premultiplied and added to what is already drawn, so the sphere glows over black.

struct U {
    rot0: vec4<f32>,
    rot1: vec4<f32>,
    rot2: vec4<f32>,
    a0: vec4<f32>,   // view.x, view.y, radius, point size (device px)
    a1: vec4<f32>,   // t, tw, flowT, clock
    a2: vec4<f32>,   // noise, freq, ripple, wave
    a3: vec4<f32>,   // scan, scanPos, swirl, flow
    a4: vec4<f32>,   // loose, thick, jitter, burst
    a5: vec4<f32>,   // flatten, sweep, sweepPos, arc
    a6: vec4<f32>,   // arcProg, amber, pulse, red
    a7: vec4<f32>,   // bright, rim, interior, 1 when blending in sRGB
    slot: vec4<f32>, // slot origin x, y and size w, h, in device px of the target
    target_px: vec4<f32>, // target width, height
}

@group(0) @binding(0) var<uniform> u: U;

struct VIn {
    @builtin(vertex_index) vid: u32,
    @location(0) dir: vec3<f32>,
    @location(1) rnd: vec4<f32>,
}

struct VOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) col: vec3<f32>,
    @location(1) a: f32,
    @location(2) size: f32,
    @location(3) uv: vec2<f32>,
}

fn mod289_3(x: vec3<f32>) -> vec3<f32> { return x - floor(x * (1.0 / 289.0)) * 289.0; }
fn mod289_4(x: vec4<f32>) -> vec4<f32> { return x - floor(x * (1.0 / 289.0)) * 289.0; }
fn permute(x: vec4<f32>) -> vec4<f32> { return mod289_4(((x * 34.0) + 1.0) * x); }
fn taylor_inv_sqrt(r: vec4<f32>) -> vec4<f32> { return 1.79284291400159 - 0.85373472095314 * r; }

// Simplex noise (Ashima Arts / Stefan Gustavson, MIT).
fn snoise(v: vec3<f32>) -> f32 {
    let C = vec2<f32>(1.0 / 6.0, 1.0 / 3.0);
    let D = vec4<f32>(0.0, 0.5, 1.0, 2.0);
    var i = floor(v + dot(v, C.yyy));
    let x0 = v - i + dot(i, C.xxx);
    let g = step(x0.yzx, x0.xyz);
    let l = 1.0 - g;
    let i1 = min(g.xyz, l.zxy);
    let i2 = max(g.xyz, l.zxy);
    let x1 = x0 - i1 + C.xxx;
    let x2 = x0 - i2 + C.yyy;
    let x3 = x0 - D.yyy;
    i = mod289_3(i);
    let p = permute(permute(permute(i.z + vec4<f32>(0.0, i1.z, i2.z, 1.0))
        + i.y + vec4<f32>(0.0, i1.y, i2.y, 1.0))
        + i.x + vec4<f32>(0.0, i1.x, i2.x, 1.0));
    let n_ = 0.142857142857;
    let ns = n_ * D.wyz - D.xzx;
    let j = p - 49.0 * floor(p * ns.z * ns.z);
    let x_ = floor(j * ns.z);
    let y_ = floor(j - 7.0 * x_);
    let x = x_ * ns.x + ns.yyyy;
    let y = y_ * ns.x + ns.yyyy;
    let h = 1.0 - abs(x) - abs(y);
    let b0 = vec4<f32>(x.xy, y.xy);
    let b1 = vec4<f32>(x.zw, y.zw);
    let s0 = floor(b0) * 2.0 + 1.0;
    let s1 = floor(b1) * 2.0 + 1.0;
    let sh = -step(h, vec4<f32>(0.0));
    let a0 = b0.xzyw + s0.xzyw * sh.xxyy;
    let a1 = b1.xzyw + s1.xzyw * sh.zzww;
    var p0 = vec3<f32>(a0.xy, h.x);
    var p1 = vec3<f32>(a0.zw, h.y);
    var p2 = vec3<f32>(a1.xy, h.z);
    var p3 = vec3<f32>(a1.zw, h.w);
    let norm = taylor_inv_sqrt(vec4<f32>(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
    p0 = p0 * norm.x;
    p1 = p1 * norm.y;
    p2 = p2 * norm.z;
    p3 = p3 * norm.w;
    var m = max(0.6 - vec4<f32>(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), vec4<f32>(0.0));
    m = m * m;
    return 42.0 * dot(m * m, vec4<f32>(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

fn rot2(v: vec2<f32>, a: f32) -> vec2<f32> {
    let c = cos(a);
    let s = sin(a);
    return vec2<f32>(c * v.x - s * v.y, s * v.x + c * v.y);
}

fn h1(n: f32) -> f32 { return fract(sin(n) * 43758.5453); }

// GLSL mod: the result has the divisor's sign, which WGSL's % does not.
fn gmod(x: f32, y: f32) -> f32 { return x - y * floor(x / y); }

@vertex
fn vs(in: VIn) -> VOut {
    let view = u.a0.xy;
    let radius = u.a0.z;
    let px = u.a0.w;
    let uT = u.a1.x;
    let uTw = u.a1.y;
    let uFlowT = u.a1.z;
    let uClock = u.a1.w;
    let uNoise = u.a2.x;
    let uFreq = u.a2.y;
    let uRipple = u.a2.z;
    let uWave = u.a2.w;
    let uScan = u.a3.x;
    let uScanPos = u.a3.y;
    let uSwirl = u.a3.z;
    let uFlow = u.a3.w;
    let uLoose = u.a4.x;
    let uThick = u.a4.y;
    let uJitter = u.a4.z;
    let uBurst = u.a4.w;
    let uFlatten = u.a5.x;
    let uSweep = u.a5.y;
    let uSweepPos = u.a5.z;
    let uArc = u.a5.w;
    let uArcProg = u.a6.x;
    let uAmber = u.a6.y;
    let uPulse = u.a6.z;
    let uRed = u.a6.w;
    let uBright = u.a7.x;
    let uRim = u.a7.y;
    let uInterior = u.a7.z;
    let rot = mat3x3<f32>(u.rot0.xyz, u.rot1.xyz, u.rot2.xyz);

    var d = in.dir;
    let r1 = in.rnd.x;
    let r2 = in.rnd.y;
    let r3 = in.rnd.z;
    let r4 = in.rnd.w;

    // thinking: latitude currents at different speeds, plus fast travelling sparks
    let band = floor((d.y * 0.5 + 0.5) * 9.0);
    let bs = (0.35 + 1.1 * h1(band * 17.31 + 3.0)) * select(-1.0, 1.0, gmod(band, 2.0) < 0.5);
    let spark = step(0.986, r3);
    let dxz = rot2(d.xz, uFlowT * (bs + spark * 2.6 * sign(r2 - 0.5)));
    d = vec3<f32>(dxz.x, d.y, dxz.y);

    // organic surface: radial and tangential noise
    let np = d * uFreq + vec3<f32>(uT * 0.11, uT * 0.17, -uT * 0.07);
    let n1 = snoise(np);
    let n2 = snoise(np * 2.6 + 11.0);
    let tang = vec3<f32>(snoise(np + 31.7), snoise(np + 57.3), snoise(np + 83.1));
    var rr = 1.0 + uNoise * (n1 * 0.8 + n2 * 0.35);
    rr = rr + 0.045 * uRipple * sin(dot(d, vec3<f32>(0.28, 0.92, 0.27)) * 10.0 - uT * 5.5 + n1 * 3.0);
    rr = rr + uThick * (r2 - 0.5) * 0.065;
    let isLoose = step(0.93, r1);
    let lph = fract(r2 * 13.7 + uT * (0.035 + 0.05 * r3));
    rr = rr + isLoose * uLoose * lph * 0.34;
    rr = rr * (1.0 + uBurst * 0.15 * (0.55 + 0.9 * r2));
    rr = rr * (1.0 + uJitter * 0.07 * (r3 - 0.35));
    var pos = d * rr + tang * uNoise * 0.45;
    let jf = floor(uClock * 30.0);
    pos = pos + (vec3<f32>(h1(r1 * 91.0 + jf), h1(r2 * 57.0 + jf * 1.3), h1(r3 * 33.0 + jf * 0.7)) - 0.5) * uJitter * 0.09;
    pos.y = pos.y * (1.0 - uFlatten * 0.14);

    var v = rot * pos;
    let nrm = rot * d;
    let rim = 1.0 - abs(nrm.z);
    let front = nrm.z * 0.5 + 0.5;
    let dd = clamp(length(v.xy), 0.0, 1.2);
    let sw = rot2(v.xy, uSwirl * 0.9 * (1.0 - dd * dd));
    let wv = sin(dd * 15.0 - uT * 9.0);
    let vxy = sw * (1.0 + uWave * wv * 0.035);
    v = vec3<f32>(vxy.x, vxy.y, v.z);
    let persp = 5.0 / (5.0 - v.z);
    let ndc = vxy * persp * radius * view;

    var a = 0.62 * mix(0.3, 1.0, pow(front, 1.3));
    a = a * mix(uInterior, 1.0, pow(rim, 2.0)) * (1.0 + uRim * pow(rim, 4.0) * 1.5);
    var size = px * mix(0.7, 1.15, front) * (0.75 + 0.5 * r3);
    let isTw = step(r1, 0.018);
    let tw = pow(max(0.0, sin(uTw * (0.7 + r2 * 1.6) + r3 * 60.0)), 14.0);
    a = a * (1.0 + isTw * (0.8 + 3.0 * tw));
    size = size * (1.0 + isTw * (0.3 + 0.8 * tw));
    a = a * mix(1.0, (1.0 - lph) * smoothstep(0.0, 0.12, lph), isLoose * uLoose);
    a = a * (1.0 + uWave * wv * 0.45);
    let sq = (vxy.y - uScanPos) / 0.07;
    let sc = exp(-sq * sq) * uScan;
    a = a * (1.0 + sc * 2.6);
    size = size * (1.0 + sc * 0.5);
    a = a * (1.0 + spark * uFlow * 3.5);
    size = size * (1.0 + spark * uFlow * 0.9);
    let lon = atan2(vxy.x, v.z);
    let dl = abs(gmod(lon - uSweepPos + 3.14159, 6.28318) - 3.14159);
    let swp = exp(-dl * dl / 0.012) * uSweep;
    a = a * (1.0 + swp * 3.0);
    size = size * (1.0 + swp * 0.4);
    let af = fract(atan2(vxy.x, vxy.y) / 6.28318 + 1.0);
    let inArc = smoothstep(0.0, 0.015, uArcProg - af);
    let hq = (af - uArcProg) / 0.02;
    let rw = pow(rim, 2.0);
    a = a * mix(1.0, mix(1.0 - 0.5 * rw, 1.0 + 3.4 * rw, inArc) + exp(-hq * hq) * 3.0 * rw * step(0.001, uArcProg), uArc);
    size = size * (1.0 + uArc * inArc * rw * 0.35);
    var col = vec3<f32>(0.95, 0.955, 0.965);
    let am = step(r4, uAmber * (0.08 + 0.34 * pow(rim, 1.5)));
    col = mix(col, vec3<f32>(0.961, 0.647, 0.141), am);
    a = a * (1.0 + am * (0.5 + 1.3 * uPulse));
    size = size * (1.0 + am * 0.25);
    let rd = step(1.0 - uRed * 0.09, r4);
    col = mix(col, vec3<f32>(0.937, 0.267, 0.267), rd);
    a = a * (1.0 + rd * 1.6);
    a = a * uBright;
    if size < 1.0 {
        a = a * size;
        size = 1.0;
    }

    // Quad corner for this vertex, then slot-local normalised coordinates to target pixels.
    var corner = vec2<f32>(0.0, 0.0);
    switch in.vid {
        case 1u, 4u: { corner = vec2<f32>(1.0, 0.0); }
        case 2u, 3u: { corner = vec2<f32>(0.0, 1.0); }
        case 5u: { corner = vec2<f32>(1.0, 1.0); }
        default: {}
    }
    let center = u.slot.xy + vec2<f32>((ndc.x * 0.5 + 0.5) * u.slot.z, (0.5 - ndc.y * 0.5) * u.slot.w);
    let p = center + (corner - 0.5) * size;
    let g = vec2<f32>(p.x / u.target_px.x * 2.0 - 1.0, 1.0 - p.y / u.target_px.y * 2.0);

    // The page's own blending is in sRGB-encoded values; a linear target needs linear colour.
    if u.a7.w < 0.5 {
        col = pow(col, vec3<f32>(2.2));
    }

    var out: VOut;
    out.pos = vec4<f32>(g, 0.0, 1.0);
    out.col = col;
    out.a = a;
    out.size = size;
    out.uv = corner;
    return out;
}

@fragment
fn fs(in: VOut) -> @location(0) vec4<f32> {
    let d = length(in.uv - vec2<f32>(0.5));
    let c = clamp((0.5 - d) * in.size + 0.5, 0.0, 1.0);
    let a = in.a * c;
    return vec4<f32>(in.col * a, a);
}
