//! OKLab mixing - the one piece of colour arithmetic the generated tables cannot carry,
//! because the chart's gradient blends a colour that depends on the data towards the
//! background. A port of termstats/theme.py, same constants, same gamut repair.

type Lab = (f64, f64, f64);

fn srgb_to_linear(c: f64) -> f64 {
    let c = c / 255.0;
    if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
}

fn linear_to_srgb(c: f64) -> f64 {
    let c = c.clamp(0.0, 1.0);
    255.0 * if c <= 0.0031308 { 12.92 * c } else { 1.055 * c.powf(1.0 / 2.4) - 0.055 }
}

fn to_oklab(rgb: u32) -> Lab {
    let r = srgb_to_linear(((rgb >> 16) & 255) as f64);
    let g = srgb_to_linear(((rgb >> 8) & 255) as f64);
    let b = srgb_to_linear((rgb & 255) as f64);
    let l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b).cbrt();
    let m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b).cbrt();
    let s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b).cbrt();
    (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
     1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
     0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)
}

fn to_linear(lab: Lab) -> (f64, f64, f64) {
    let (lightness, a, b) = lab;
    let l = (lightness + 0.3963377774 * a + 0.2158037573 * b).powi(3);
    let m = (lightness - 0.1055613458 * a - 0.0638541728 * b).powi(3);
    let s = (lightness - 0.0894841775 * a - 1.2914855480 * b).powi(3);
    (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
     -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
     -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
}

fn in_gamut(lab: Lab) -> bool {
    let (r, g, b) = to_linear(lab);
    [r, g, b].iter().all(|c| (-1e-6..=1.0 + 1e-6).contains(c))
}

fn to_rgb(lab: Lab) -> u32 {
    let (r, g, b) = to_linear(lab);
    let ch = |c: f64| linear_to_srgb(c).round() as u32;
    (ch(r) << 16) | (ch(g) << 8) | ch(b)
}

/// `a` blended toward `b` by `k` (0 = a, 1 = b). Out-of-gamut results lose chroma, never
/// a clipped channel - clipping shifts the hue.
pub fn mix(a: u32, b: u32, k: f64) -> u32 {
    let k = k.clamp(0.0, 1.0);
    let (la, lb) = (to_oklab(a), to_oklab(b));
    let lab = (la.0 + (lb.0 - la.0) * k, la.1 + (lb.1 - la.1) * k, la.2 + (lb.2 - la.2) * k);
    if in_gamut(lab) {
        return to_rgb(lab);
    }
    let (mut lo, mut hi) = (0.0f64, 1.0f64);
    for _ in 0..24 {
        let mid = (lo + hi) / 2.0;
        if in_gamut((lab.0, lab.1 * mid, lab.2 * mid)) { lo = mid } else { hi = mid }
    }
    to_rgb((lab.0, lab.1 * lo, lab.2 * lo))
}

/// The nearest entry of the xterm 256-colour palette (cube or grey ramp).
pub fn to_256(rgb: u32) -> u8 {
    let (r, g, b) = (((rgb >> 16) & 255) as i32, ((rgb >> 8) & 255) as i32, (rgb & 255) as i32);
    const LEVELS: [i32; 6] = [0, 95, 135, 175, 215, 255];
    let nearest = |v: i32| (0..6).min_by_key(|&i| (LEVELS[i] - v).abs()).unwrap_or(0);
    let (ri, gi, bi) = (nearest(r), nearest(g), nearest(b));
    let cube = (LEVELS[ri], LEVELS[gi], LEVELS[bi]);
    let grey_i = (((r + g + b) / 3 - 8).clamp(0, 230) + 5) / 10;
    let grey = 8 + 10 * grey_i.min(23);
    let dist = |c: (i32, i32, i32)| (c.0 - r).pow(2) + (c.1 - g).pow(2) + (c.2 - b).pow(2);
    if dist((grey, grey, grey)) < dist(cube) {
        (232 + grey_i.min(23)) as u8
    } else {
        (16 + 36 * ri + 6 * gi + bi) as u8
    }
}
