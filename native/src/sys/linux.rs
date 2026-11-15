use super::{usage_percent, CpuTimes, Disk, Mem, Net, ProcSample, Vitals, SKIP_FS};
use std::fs;
use std::io::Read;

pub const OS_NAME: &str = "Linux";
pub const HAS_STEAL: bool = true;

fn read_into(path: &str, buf: &mut String) -> bool {
    buf.clear();
    match fs::File::open(path) {
        Ok(mut f) => f.read_to_string(buf).is_ok(),
        Err(_) => false,
    }
}

/// Per-core ticks into `cores`; returns (all cores together, steal ticks of all cores).
pub fn cpu_times(cores: &mut Vec<CpuTimes>) -> (CpuTimes, u64) {
    cores.clear();
    let mut all = CpuTimes::default();
    let mut steal = 0u64;
    let mut text = String::new();
    if !read_into("/proc/stat", &mut text) {
        return (all, 0);
    }
    for line in text.lines() {
        if !line.starts_with("cpu") {
            break;
        }
        let mut fields = line.split_ascii_whitespace();
        let name = fields.next().unwrap_or("");
        // user nice system idle iowait irq softirq steal - guest time is already inside user.
        let v: Vec<u64> = fields.take(8).map(|f| f.parse().unwrap_or(0)).collect();
        if v.len() < 4 {
            continue;
        }
        let total: u64 = v.iter().sum();
        let idle = v[3] + v.get(4).copied().unwrap_or(0);
        let times = CpuTimes { busy: total - idle, total };
        if name == "cpu" {
            all = times;
            steal = v.get(7).copied().unwrap_or(0);
        } else {
            cores.push(times);
        }
    }
    (all, steal)
}

pub fn memory() -> Mem {
    let mut text = String::new();
    let mut mem = Mem::default();
    if !read_into("/proc/meminfo", &mut text) {
        return mem;
    }
    let (mut free, mut buffers, mut cached, mut srecl, mut swap_free) = (0u64, 0u64, 0u64, 0u64, 0u64);
    let mut have_avail = false;
    for line in text.lines() {
        let mut it = line.split_ascii_whitespace();
        let (key, value) = (it.next().unwrap_or(""), it.next().and_then(|v| v.parse::<u64>().ok()).unwrap_or(0) * 1024);
        match key {
            "MemTotal:" => mem.total = value,
            "MemFree:" => free = value,
            "MemAvailable:" => { mem.available = value; have_avail = true }
            "Buffers:" => buffers = value,
            "Cached:" => cached = value,
            "SReclaimable:" => srecl = value,
            "SwapTotal:" => mem.swap_total = value,
            "SwapFree:" => swap_free = value,
            _ => {}
        }
    }
    if !have_avail {
        mem.available = free + buffers + cached;
    }
    // psutil's "used": total - free - buffers - cached (reclaimable slab counted as cache).
    mem.used = mem.total.saturating_sub(free + buffers + cached + srecl);
    mem.swap_used = mem.swap_total.saturating_sub(swap_free);
    mem
}

pub fn disks() -> Vec<Disk> {
    let mut out: Vec<Disk> = Vec::new();
    let (mut mounts, mut fstypes) = (String::new(), String::new());
    if !read_into("/proc/mounts", &mut mounts) {
        return out;
    }
    // Only filesystems that sit on a device: /proc/filesystems marks the others "nodev".
    read_into("/proc/filesystems", &mut fstypes);
    let physical: Vec<&str> = fstypes.lines().filter(|l| !l.starts_with("nodev"))
        .map(|l| l.trim()).filter(|l| !l.is_empty()).collect();
    for line in mounts.lines() {
        let mut it = line.split_ascii_whitespace();
        let (_dev, mount, fstype) = (it.next().unwrap_or(""), it.next().unwrap_or(""), it.next().unwrap_or(""));
        if mount.is_empty() || SKIP_FS.contains(&fstype) || (fstype != "zfs" && !physical.contains(&fstype)) {
            continue;
        }
        let mount = mount.replace("\\040", " ");
        if out.iter().any(|d| d.mount == mount) {
            continue;
        }
        let Ok(path) = std::ffi::CString::new(mount.as_bytes()) else { continue };
        let mut vfs: libc::statvfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statvfs(path.as_ptr(), &mut vfs) } != 0 {
            continue;
        }
        let block = vfs.f_frsize as u64;
        let total = vfs.f_blocks as u64 * block;
        let used = (vfs.f_blocks as u64).saturating_sub(vfs.f_bfree as u64) * block;
        out.push(Disk { mount, total, used, percent: usage_percent(used, vfs.f_bavail as u64 * block) });
    }
    out
}

/// Bytes sent and received by all interfaces (`_buf` is what macOS keeps its list in).
pub fn net(_buf: &mut Vec<u8>) -> Net {
    let mut text = String::new();
    let mut total = Net::default();
    if !read_into("/proc/net/dev", &mut text) {
        return total;
    }
    for line in text.lines().skip(2) {
        let Some((_name, rest)) = line.split_once(':') else { continue };
        let v: Vec<u64> = rest.split_ascii_whitespace().map(|f| f.parse().unwrap_or(0)).collect();
        if v.len() >= 9 {
            total.recv += v[0];
            total.sent += v[8];
        }
    }
    total
}

/// Bytes read and written by whole disks (partitions would count every byte twice).
pub fn disk_io() -> Option<(u64, u64)> {
    let mut text = String::new();
    if !read_into("/proc/diskstats", &mut text) {
        return None;
    }
    let (mut read, mut written, mut any) = (0u64, 0u64, false);
    for line in text.lines() {
        let f: Vec<&str> = line.split_ascii_whitespace().collect();
        if f.len() < 10 || !std::path::Path::new("/sys/block").join(f[2]).exists() {
            continue;
        }
        read += f[5].parse::<u64>().unwrap_or(0) * 512;
        written += f[9].parse::<u64>().unwrap_or(0) * 512;
        any = true;
    }
    if any { Some((read, written)) } else { None }
}

pub fn boot_time() -> f64 {
    let mut text = String::new();
    if read_into("/proc/uptime", &mut text) {
        if let Some(up) = text.split_ascii_whitespace().next().and_then(|v| v.parse::<f64>().ok()) {
            return super::now() - up;
        }
    }
    0.0
}

/// Every process; returns how many there are.
pub fn proc_scan(pids: &mut Vec<i32>, out: &mut Vec<ProcSample>) -> usize {
    out.clear();
    pids.clear();
    let tick_ns = 1_000_000_000u64 / (unsafe { libc::sysconf(libc::_SC_CLK_TCK) }.max(1) as u64);
    let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) }.max(1) as u64;
    let Ok(dir) = fs::read_dir("/proc") else { return 0 };
    let mut text = String::new();
    let mut path = String::new();
    for entry in dir.flatten() {
        let name = entry.file_name();
        let Some(pid) = name.to_str().and_then(|s| s.parse::<i32>().ok()) else { continue };
        pids.push(pid);
        path.clear();
        path.push_str("/proc/");
        path.push_str(name.to_str().unwrap_or(""));
        path.push_str("/stat");
        if !read_into(&path, &mut text) {
            continue;
        }
        // The command may contain spaces and parentheses; the fields start after the LAST ")".
        let Some(close) = text.rfind(')') else { continue };
        let f: Vec<&str> = text[close + 1..].split_ascii_whitespace().collect();
        if f.len() < 22 {
            continue;
        }
        let ticks = f[11].parse::<u64>().unwrap_or(0) + f[12].parse::<u64>().unwrap_or(0);
        let rss_pages = f[21].parse::<u64>().unwrap_or(0);
        out.push(ProcSample { pid, cpu_ns: ticks * tick_ns, rss: rss_pages * page });
    }
    pids.len()
}

/// The command name - asked for the handful of rows that are drawn, never for all.
pub fn proc_name(pid: i32) -> String {
    let mut text = String::new();
    let mut comm = String::new();
    if read_into(&format!("/proc/{pid}/comm"), &mut text) {
        comm = text.trim_end().to_string();
    }
    // The kernel cuts comm at 15 characters; the command line knows the whole name.
    if comm.len() >= 15 && read_into(&format!("/proc/{pid}/cmdline"), &mut text) {
        if let Some(first) = text.split('\0').next() {
            let base = first.rsplit('/').next().unwrap_or(first);
            if base.starts_with(&comm) {
                return base.to_string();
            }
        }
    }
    if comm.is_empty() { "?".to_string() } else { comm }
}

// --- what the recorder reads besides the dashboard's numbers --------------------------------

fn read_trimmed(path: &std::path::Path) -> Option<String> {
    fs::read_to_string(path).ok().map(|s| s.trim().to_string())
}

/// Memory pressure from PSI: the share of the last ten seconds in which some task waited
/// for memory. Under 5 % is normal, under 25 % a warning, above that critical.
fn memory_pressure() -> Option<u8> {
    let text = fs::read_to_string("/proc/pressure/memory").ok()?;
    let some = text.lines().find(|l| l.starts_with("some"))?;
    let avg10: f64 = some.split_ascii_whitespace().find_map(|f| f.strip_prefix("avg10="))?.parse().ok()?;
    Some(if avg10 >= 25.0 { 2 } else if avg10 >= 5.0 { 1 } else { 0 })
}

fn drm_entries() -> Vec<std::path::PathBuf> {
    fs::read_dir("/sys/class/drm").map(|d| d.flatten().map(|e| e.path()).collect()).unwrap_or_default()
}

/// `full` also asks for the power source and the displays (see the macOS twin).
pub fn vitals(full: bool) -> Vitals {
    let mut v = Vitals { pressure: memory_pressure(), ..Vitals::default() };
    let drm = drm_entries();
    // amdgpu and a few others publish a busy percentage; where none does, the field stays empty.
    v.gpu_pct = drm.iter().filter_map(|card| read_trimmed(&card.join("device/gpu_busy_percent"))?.parse::<f64>().ok())
        .fold(None, |best: Option<f64>, pct| Some(best.map_or(pct, |b| b.max(pct))));
    if !full {
        return v;
    }
    // Connectors are named card0-HDMI-A-1, card0-eDP-1, ...; the built-in panel is eDP, LVDS or DSI.
    let connectors: Vec<_> = drm.iter().filter(|p| p.join("status").exists()).collect();
    if !connectors.is_empty() {
        let external = connectors.iter().filter(|p| {
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
            let builtin = ["eDP", "LVDS", "DSI"].iter().any(|k| name.contains(k));
            !builtin && read_trimmed(&p.join("status")).as_deref() == Some("connected")
        }).count();
        v.ext_displays = Some(external.min(255) as u8);
    }
    if let Ok(supplies) = fs::read_dir("/sys/class/power_supply") {
        for supply in supplies.flatten().map(|e| e.path()) {
            match read_trimmed(&supply.join("type")).as_deref() {
                Some("Mains") | Some("USB") => {
                    if let Some(online) = read_trimmed(&supply.join("online")) {
                        v.on_ac = Some(v.on_ac.unwrap_or(false) || online == "1");
                    }
                }
                Some("Battery") => {
                    v.battery_pct = read_trimmed(&supply.join("capacity")).and_then(|c| c.parse::<u8>().ok()).or(v.battery_pct);
                }
                _ => {}
            }
        }
        // A battery without a reported mains supply: discharging means on battery.
        if v.battery_pct.is_some() && v.on_ac.is_none() {
            v.on_ac = Some(true);
        }
    }
    v
}

/// (model, chip name), e.g. ("Raspberry Pi 5 Model B Rev 1.0", "Cortex-A76").
pub fn machine() -> (String, String) {
    let model = ["/sys/devices/virtual/dmi/id/product_name", "/proc/device-tree/model"].iter()
        .find_map(|p| read_trimmed(std::path::Path::new(p)))
        .map(|m| m.trim_end_matches('\0').to_string()).unwrap_or_default();
    let cpuinfo = fs::read_to_string("/proc/cpuinfo").unwrap_or_default();
    let chip = ["model name", "Model", "Hardware", "cpu model"].iter()
        .find_map(|key| cpuinfo.lines().find(|l| l.starts_with(key)).and_then(|l| l.split_once(':')).map(|(_, v)| v.trim().to_string()))
        .unwrap_or_default();
    (model, chip)
}
