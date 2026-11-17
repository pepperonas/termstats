//! An iterative radix-2 FFT - the one piece of numpy the microphone screens needed.
//!
//! Real input of length `n` (a power of two) is transformed as a complex sequence; only the
//! magnitudes of bins 0..=n/2 are kept, which is what `np.abs(np.fft.rfft(x))` returns.
//! The twiddle factors and the bit-reversal table are made once per size.

pub struct Fft {
    n: usize,
    rev: Vec<usize>,
    cos: Vec<f64>,
    sin: Vec<f64>,
    re: Vec<f64>,
    im: Vec<f64>,
}

impl Fft {
    pub fn new(n: usize) -> Self {
        assert!(n >= 2 && n.is_power_of_two(), "FFT size must be a power of two, got {n}");
        let bits = n.trailing_zeros();
        let rev = (0..n).map(|i| i.reverse_bits() >> (usize::BITS - bits)).collect();
        let (cos, sin) = (0..n / 2)
            .map(|k| {
                let a = -2.0 * std::f64::consts::PI * k as f64 / n as f64;
                (a.cos(), a.sin())
            })
            .unzip();
        Fft { n, rev, cos, sin, re: vec![0.0; n], im: vec![0.0; n] }
    }

    pub fn len(&self) -> usize {
        self.n
    }

    pub fn is_empty(&self) -> bool {
        false
    }

    /// `out[k] = |X[k]|` for k in 0..=n/2. `windowed` is the already windowed input; a
    /// shorter input is zero-padded, `out` shorter than n/2+1 is filled as far as it goes.
    pub fn magnitudes(&mut self, windowed: &[f32], out: &mut [f32]) {
        let n = self.n;
        for i in 0..n {
            self.re[self.rev[i]] = windowed.get(i).copied().unwrap_or(0.0) as f64;
            self.im[self.rev[i]] = 0.0;
        }
        let mut size = 2;
        while size <= n {
            let half = size / 2;
            let step = n / size;
            for start in (0..n).step_by(size) {
                for k in 0..half {
                    let (wr, wi) = (self.cos[k * step], self.sin[k * step]);
                    let (a, b) = (start + k, start + k + half);
                    let tr = self.re[b] * wr - self.im[b] * wi;
                    let ti = self.re[b] * wi + self.im[b] * wr;
                    self.re[b] = self.re[a] - tr;
                    self.im[b] = self.im[a] - ti;
                    self.re[a] += tr;
                    self.im[a] += ti;
                }
            }
            size *= 2;
        }
        for (k, slot) in out.iter_mut().take(n / 2 + 1).enumerate() {
            *slot = self.re[k].hypot(self.im[k]) as f32;
        }
    }
}
