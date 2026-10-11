/**
 * The build's projections, bit for bit: PROJ 9.5.1's own arithmetic for the pipelines pyproj runs from
 * EPSG:5070 (NAD83 / Conus Albers) to a 3DEP tile's CRS (pyproj 3.7.2, `Transformer.from_crs(5070, epsg,
 * always_xy=True)`):
 *
 *   +proj=pipeline +step +inv +proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +ellps=GRS80
 *     +step +proj=utm +zone=<z> +ellps=GRS80          (NAD83 / UTM zone z, EPSG:269zz, zones 4 to 20)
 *     +step +proj=unitconvert +xy_in=rad +xy_out=deg   (NAD83 geographic, EPSG:4269: longitude, latitude)
 *
 * Ported from aea.cpp (setup, aea_e_inverse, phi1_), tmerc.cpp (utm, setup_exact, exact_e_fwd, gatg, clenS,
 * clens), inv.cpp and fwd.cpp (inv_prepare, inv_finalize, fwd_prepare, fwd_finalize), ell_set.cpp
 * (pj_calc_ellipsoid_params), qsfn.cpp, msfn.cpp, adjlon.cpp, dmstor.cpp (degrees to radians) and
 * unitconvert.cpp, keeping C's order of operations: a float64 result depends on it. proj4js computes the
 * same projections another way and lands about 1e-9 m off, which flips a float32 sample of a pull now
 * and then (2026-10-11).
 */

const DEG_TO_RAD = 0.017453292519943296 // proj_internal.h
const M_HALFPI = 1.5707963267948966 // M_PI_2
const M_TWOPI = 6.283185307179586

// GRS80 (ellps.cpp: a=6378137.0 rf=298.257222101; ell_set.cpp ellps_shape and pj_calc_ellipsoid_params)
const A = 6378137.0
const RF = 298.257222101
const F = 1 / RF
const ES = 2 * F - F * F
const E = Math.sqrt(ES)
const ALPHA = Math.asin(E)
const TAN_HALF_ALPHA = Math.tan(ALPHA / 2)
const N3 = TAN_HALF_ALPHA * TAN_HALF_ALPHA // pow(tan(alpha / 2), 2): the third flattening
const RA = 1 / A
const ONE_ES = 1 - ES

export function adjlon(longitude: number): number {
  if (Math.abs(longitude) < Math.PI + 1e-12) return longitude
  longitude += Math.PI
  longitude -= M_TWOPI * Math.floor(longitude / M_TWOPI)
  longitude -= Math.PI
  return longitude
}

function qsfn(sinphi: number, e: number, oneEs: number): number {
  const con = e * sinphi
  const div1 = 1.0 - con * con
  const div2 = 1.0 + con
  return oneEs * (sinphi / div1 - (0.5 / e) * Math.log((1 - con) / div2))
}

function msfn(sinphi: number, cosphi: number, es: number): number {
  return cosphi / Math.sqrt(1 - es * sinphi * sinphi)
}

// ------------------------------------------------------------------ Albers (EPSG:5070), inverse
const AEA_PHI0 = 23 * DEG_TO_RAD // dmstor: 0. + 23 * DEG_TO_RAD
const AEA_LAM0 = -(96 * DEG_TO_RAD)
const AEA = (() => {
  const phi1 = 29.5 * DEG_TO_RAD
  const phi2 = 45.5 * DEG_TO_RAD
  let sinphi = Math.sin(phi1)
  let cosphi = Math.cos(phi1)
  const m1 = msfn(sinphi, cosphi, ES)
  const ml1 = qsfn(sinphi, E, ONE_ES)
  sinphi = Math.sin(phi2)
  cosphi = Math.cos(phi2)
  const m2 = msfn(sinphi, cosphi, ES)
  const ml2 = qsfn(sinphi, E, ONE_ES)
  const n = (m1 * m1 - m2 * m2) / (ml2 - ml1)
  const ec = 1 - 0.5 * ONE_ES * Math.log((1 - E) / (1 + E)) / E
  const c = m1 * m1 + n * ml1
  const dd = 1 / n
  const rho0 = dd * Math.sqrt(c - n * qsfn(Math.sin(AEA_PHI0), E, ONE_ES))
  return { n, ec, c, dd, rho0 }
})()

function phi1(qs: number, te: number, toneEs: number): number {
  let phi = Math.asin(0.5 * qs)
  if (te < 1.0e-7) return phi
  let i = 15
  do {
    const sinpi = Math.sin(phi)
    const cospi = Math.cos(phi)
    const con = te * sinpi
    const com = 1 - con * con
    const dphi = 0.5 * com * com / cospi * (qs / toneEs - sinpi / com + 0.5 / te * Math.log((1 - con) / (1 + con)))
    phi += dphi
    if (!(Math.abs(dphi) > 1.0e-10)) return phi
    --i
  } while (i >= 0)
  return Infinity
}

/** EPSG:5070 metres to NAD83 longitude and latitude in radians (pj_inv: inv_prepare, aea_e_inverse,
 * inv_finalize). */
export function albersInverse(x: number, y: number): [number, number] {
  // inv_prepare, classic units: (to_meter * x - x0) * ra
  let xx = 1 * x - 0
  let yy = 1 * y - 0
  xx *= RA
  yy *= RA
  // aea_e_inverse (ellipsoidal, n > 0)
  yy = AEA.rho0 - yy
  const rho = hypot(xx, yy)
  let lam = 0
  let phi: number
  if (rho !== 0) {
    phi = rho / AEA.dd
    phi = (AEA.c - phi * phi) / AEA.n
    if (Math.abs(AEA.ec - Math.abs(phi)) > 1e-7) {
      if (Math.abs(phi) > 2) return [Infinity, Infinity]
      phi = phi1(phi, E, ONE_ES)
    } else {
      phi = phi < 0 ? -M_HALFPI : M_HALFPI
    }
    lam = Math.atan2(xx, yy) / AEA.n
  } else {
    phi = M_HALFPI
  }
  // inv_finalize: lam + from_greenwich + lam0, then adjlon
  lam = lam + 0 + AEA_LAM0
  lam = adjlon(lam)
  return [lam, phi]
}

// ------------------------------------------------------------------ UTM (Poder/Engsager), forward
function gatg(p1: number[], b: number, cos2B: number, sin2B: number): number {
  let h = 0
  let h2 = 0
  const twoCos2B = 2 * cos2B
  let p = p1.length
  let h1 = p1[--p]
  while (p) {
    h = -h2 + twoCos2B * h1 + p1[--p]
    h2 = h1
    h1 = h
  }
  return b + h * sin2B
}

/** Complex Clenshaw summation; [R, I]. */
function clenS(a: number[], sinArgR: number, cosArgR: number, sinhArgI: number, coshArgI: number): [number, number] {
  let p = a.length
  let r = 2 * cosArgR * coshArgI
  let i = -2 * sinArgR * sinhArgI
  let hi1 = 0
  let hr1 = 0
  let hi = 0
  let hr = a[--p]
  let hr2: number
  let hi2: number
  while (p) {
    hr2 = hr1
    hi2 = hi1
    hr1 = hr
    hi1 = hi
    hr = -hr2 + r * hr1 - i * hi1 + a[--p]
    hi = -hi2 + i * hr1 + r * hi1
  }
  r = sinArgR * coshArgI
  i = cosArgR * sinhArgI
  return [r * hr - i * hi, r * hi + i * hr]
}

function clens(a: number[], argR: number): number {
  let p = a.length
  const r = 2 * Math.cos(argR)
  let hr1 = 0
  let hr = a[--p]
  let hr2: number
  while (p) {
    hr2 = hr1
    hr1 = hr
    hr = -hr2 + r * hr1 + a[--p]
  }
  return Math.sin(argR) * hr
}

const TM = (() => {
  // setup_exact with n = P->n, k0 = 0.9996 (utm), phi0 = 0
  const n = N3
  const k0 = 0.9996
  let np = n
  const cgb = new Array<number>(6)
  const cbg = new Array<number>(6)
  const utg = new Array<number>(6)
  const gtu = new Array<number>(6)
  cgb[0] = n * (2 + n * (-2 / 3.0 + n * (-2 + n * (116 / 45.0 + n * (26 / 45.0 + n * (-2854 / 675.0))))))
  cbg[0] = n * (-2 + n * (2 / 3.0 + n * (4 / 3.0 + n * (-82 / 45.0 + n * (32 / 45.0 + n * (4642 / 4725.0))))))
  np *= n
  cgb[1] = np * (7 / 3.0 + n * (-8 / 5.0 + n * (-227 / 45.0 + n * (2704 / 315.0 + n * (2323 / 945.0)))))
  cbg[1] = np * (5 / 3.0 + n * (-16 / 15.0 + n * (-13 / 9.0 + n * (904 / 315.0 + n * (-1522 / 945.0)))))
  np *= n
  cgb[2] = np * (56 / 15.0 + n * (-136 / 35.0 + n * (-1262 / 105.0 + n * (73814 / 2835.0))))
  cbg[2] = np * (-26 / 15.0 + n * (34 / 21.0 + n * (8 / 5.0 + n * (-12686 / 2835.0))))
  np *= n
  cgb[3] = np * (4279 / 630.0 + n * (-332 / 35.0 + n * (-399572 / 14175.0)))
  cbg[3] = np * (1237 / 630.0 + n * (-12 / 5.0 + n * (-24832 / 14175.0)))
  np *= n
  cgb[4] = np * (4174 / 315.0 + n * (-144838 / 6237.0))
  cbg[4] = np * (-734 / 315.0 + n * (109598 / 31185.0))
  np *= n
  cgb[5] = np * (601676 / 22275.0)
  cbg[5] = np * (444337 / 155925.0)
  np = n * n
  const qn = k0 / (1 + n) * (1 + np * (1 / 4.0 + np * (1 / 64.0 + np / 256.0)))
  utg[0] = n * (-0.5 + n * (2 / 3.0 + n * (-37 / 96.0 + n * (1 / 360.0 + n * (81 / 512.0 + n * (-96199 / 604800.0))))))
  gtu[0] = n * (0.5 + n * (-2 / 3.0 + n * (5 / 16.0 + n * (41 / 180.0 + n * (-127 / 288.0 + n * (7891 / 37800.0))))))
  utg[1] = np * (-1 / 48.0 + n * (-1 / 15.0 + n * (437 / 1440.0 + n * (-46 / 105.0 + n * (1118711 / 3870720.0)))))
  gtu[1] = np * (13 / 48.0 + n * (-3 / 5.0 + n * (557 / 1440.0 + n * (281 / 630.0 + n * (-1983433 / 1935360.0)))))
  np *= n
  utg[2] = np * (-17 / 480.0 + n * (37 / 840.0 + n * (209 / 4480.0 + n * (-5569 / 90720.0))))
  gtu[2] = np * (61 / 240.0 + n * (-103 / 140.0 + n * (15061 / 26880.0 + n * (167603 / 181440.0))))
  np *= n
  utg[3] = np * (-4397 / 161280.0 + n * (11 / 504.0 + n * (830251 / 7257600.0)))
  gtu[3] = np * (49561 / 161280.0 + n * (-179 / 168.0 + n * (6601661 / 7257600.0)))
  np *= n
  utg[4] = np * (-4583 / 161280.0 + n * (108847 / 3991680.0))
  gtu[4] = np * (34729 / 80640.0 + n * (-3418889 / 1995840.0))
  np *= n
  utg[5] = np * (-20648693 / 638668800.0)
  gtu[5] = np * (212378941 / 319334400.0)
  const phi0 = 0
  const z = gatg(cbg, phi0, Math.cos(2 * phi0), Math.sin(2 * phi0))
  const zb = -qn * (z + clens(gtu, 2 * z))
  return { cbg, gtu, qn, zb }
})()

/** The central meridian of UTM zone `zone` (tmerc.cpp, PJ_PROJECTION(utm)). */
function utmLam0(zone: number): number {
  const z = zone - 1
  return (z + 0.5) * Math.PI / 30 - Math.PI
}

/** NAD83 longitude and latitude (radians) to UTM zone `zone` metres (pj_fwd: fwd_prepare, exact_e_fwd,
 * fwd_finalize). */
export function utmForward(zone: number, lam: number, phi: number): [number, number] {
  // fwd_prepare (radians in; over = 0, from_greenwich = 0)
  lam = adjlon(lam)
  lam = (lam - 0) - utmLam0(zone)
  lam = adjlon(lam)
  // exact_e_fwd
  let cn = gatg(TM.cbg, phi, Math.cos(2 * phi), Math.sin(2 * phi))
  const sinCn = Math.sin(cn)
  const cosCn = Math.cos(cn)
  const sinCe = Math.sin(lam)
  const cosCe = Math.cos(lam)
  const cosCnCosCe = cosCn * cosCe
  cn = Math.atan2(sinCn, cosCnCosCe)
  const invDenomTanCe = 1 / hypot(sinCn, cosCnCosCe)
  const tanCe = sinCe * cosCn * invDenomTanCe
  let ce = Math.asinh(tanCe)
  const twoInvDenomTanCe = 2 * invDenomTanCe
  const twoInvDenomTanCeSquare = twoInvDenomTanCe * invDenomTanCe
  const tmpR = cosCnCosCe * twoInvDenomTanCeSquare
  const sinArgR = sinCn * tmpR
  const cosArgR = cosCnCosCe * tmpR - 1
  const sinhArgI = tanCe * twoInvDenomTanCe
  const coshArgI = twoInvDenomTanCeSquare - 1
  const [dCn, dCe] = clenS(TM.gtu, sinArgR, cosArgR, sinhArgI, coshArgI)
  cn += dCn
  ce += dCe
  if (!(Math.abs(ce) <= 2.623395162778)) return [Infinity, Infinity]
  let y = TM.qn * cn + TM.zb
  let x = TM.qn * ce
  // fwd_finalize, classic units: * a, then fr_meter * (v + v0)
  x *= A
  y *= A
  x = 1 * (x + 500000)
  y = 1 * (y + 0)
  return [x, y]
}

const RAD_TO_DEG_FACTOR = 1.0 / DEG_TO_RAD // unitconvert: xy_factor = 1.0 (rad) / DEG_TO_RAD (deg)

/** EPSG:5070 metres to the CRS `epsg` (a NAD83 UTM zone, 269zz, or NAD83 geographic, 4269), as pyproj
 * gives them: easting, northing, or longitude, latitude in degrees. */
export function from5070(epsg: number, X: ArrayLike<number>, Y: ArrayLike<number>): [Float64Array, Float64Array] {
  const n = X.length
  const u = new Float64Array(n)
  const v = new Float64Array(n)
  const zone = epsg - 26900
  if (epsg !== 4269 && !(zone >= 1 && zone <= 23)) throw new Error(`no exact projection from EPSG:5070 to EPSG:${epsg}`)
  for (let i = 0; i < n; i++) {
    const [lam, phi] = albersInverse(X[i], Y[i])
    if (epsg === 4269) {
      u[i] = lam * RAD_TO_DEG_FACTOR
      v[i] = phi * RAD_TO_DEG_FACTOR
    } else {
      ;[u[i], v[i]] = utmForward(zone, lam, phi)
    }
  }
  return [u, v]
}

/** The C library's hypot as PROJ calls it: the Windows C runtime's gives sqrt(x * x + y * y) here (no
 * difference on 20,000 points, checked through ctypes), and Math.hypot rounds otherwise more often. */
export let hypot = (x: number, y: number): number => Math.sqrt(x * x + y * y)

/** For the test that compares hypot variants. */
export function setHypot(f: (x: number, y: number) => number): void {
  hypot = f
}
