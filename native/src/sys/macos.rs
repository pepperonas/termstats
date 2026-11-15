use super::{usage_percent, CpuTimes, Disk, Mem, Net, ProcSample, Vitals, SKIP_FS};
use std::ffi::CStr;
use std::mem::{size_of, zeroed};
use std::sync::OnceLock;

pub const OS_NAME: &str = "macOS";
pub const HAS_STEAL: bool = false;
const DATA_VOLUME: &[u8] = b"/System/Volumes/Data\0";

fn host() -> libc::mach_port_t {
    // mach_host_self() hands out a new port right on every call; ask once.
    static HOST: OnceLock<libc::mach_port_t> = OnceLock::new();
    #[allow(deprecated)]
    *HOST.get_or_init(|| unsafe { libc::mach_host_self() })
}

fn sysctl_value<T: Copy>(name: &[u8]) -> Option<T> {
    unsafe {
        let mut value: T = zeroed();
        let mut len = size_of::<T>();
        let rc = libc::sysctlbyname(name.as_ptr() as *const libc::c_char, &mut value as *mut T as *mut libc::c_void,
                                    &mut len, std::ptr::null_mut(), 0);
        if rc == 0 { Some(value) } else { None }
    }
}

/// Per-core ticks into `cores`; returns (all cores together, steal ticks - none on macOS).
pub fn cpu_times(cores: &mut Vec<CpuTimes>) -> (CpuTimes, u64) {
    cores.clear();
    let mut all = CpuTimes::default();
    unsafe {
        let mut ncpu: libc::natural_t = 0;
        let mut info: libc::processor_info_array_t = std::ptr::null_mut();
        let mut count: libc::mach_msg_type_number_t = 0;
        if libc::host_processor_info(host(), libc::PROCESSOR_CPU_LOAD_INFO, &mut ncpu, &mut info, &mut count) != 0 {
            return (all, 0);
        }
        let states = libc::CPU_STATE_MAX as usize;
        let ticks = std::slice::from_raw_parts(info as *const u32, ncpu as usize * states);
        for cpu in ticks.chunks_exact(states) {
            let (user, system, idle, nice) = (cpu[0] as u64, cpu[1] as u64, cpu[2] as u64, cpu[3] as u64);
            let busy = user + system + nice;
            cores.push(CpuTimes { busy, total: busy + idle });
            all.busy += busy;
            all.total += busy + idle;
        }
        #[allow(deprecated)]
        libc::vm_deallocate(libc::mach_task_self(), info as libc::vm_address_t,
                            (count as usize * size_of::<libc::integer_t>()) as libc::vm_size_t);
    }
    (all, 0)
}

pub fn memory() -> Mem {
    let mut mem = Mem { total: sysctl_value::<u64>(b"hw.memsize\0").unwrap_or(0), ..Mem::default() };
    unsafe {
        let mut vm: libc::vm_statistics64 = zeroed();
        let mut count = (size_of::<libc::vm_statistics64>() / size_of::<libc::integer_t>()) as libc::mach_msg_type_number_t;
        if libc::host_statistics64(host(), libc::HOST_VM_INFO64, &mut vm as *mut _ as libc::host_info64_t, &mut count) == 0 {
            let page = libc::sysconf(libc::_SC_PAGESIZE) as u64;
            // The same arithmetic psutil uses, so the two dashboards agree to the digit.
            mem.available = (vm.inactive_count as u64 + vm.free_count as u64) * page;
            mem.used = (vm.active_count as u64 + vm.wire_count as u64) * page;
            mem.compressed = vm.compressor_page_count as u64 * page;
            mem.uncompressed = vm.total_uncompressed_pages_in_compressor * page;
        }
    }
    if let Some(swap) = sysctl_value::<libc::xsw_usage>(b"vm.swapusage\0") {
        mem.swap_total = swap.xsu_total;
        mem.swap_used = swap.xsu_used;
    }
    mem
}

/// The bytes THIS volume occupies. APFS volumes share one container, so "blocks minus free
/// blocks" is the same large number for every volume in it - an empty volume would read
/// as full. The volume attribute is the per-volume figure (the one `df` and psutil show).
fn volume_space_used(path: *const libc::c_char) -> Option<u64> {
    #[repr(C, packed(4))]
    struct Reply {
        size: u32,
        used: u64,
    }
    unsafe {
        let mut attrs: libc::attrlist = zeroed();
        attrs.bitmapcount = libc::ATTR_BIT_MAP_COUNT;
        attrs.volattr = libc::ATTR_VOL_INFO | libc::ATTR_VOL_SPACEUSED;
        let mut reply = Reply { size: 0, used: 0 };
        let rc = libc::getattrlist(path, &mut attrs as *mut _ as *mut libc::c_void,
                                   &mut reply as *mut _ as *mut libc::c_void, size_of::<Reply>(), 0);
        if rc == 0 { Some(reply.used) } else { None }
    }
}

fn usage_of(fs: &libc::statfs) -> (u64, u64, f64) {
    let block = fs.f_bsize as u64;
    let total = fs.f_blocks * block;
    let used = volume_space_used(fs.f_mntonname.as_ptr())
        .unwrap_or_else(|| fs.f_blocks.saturating_sub(fs.f_bfree) * block);
    (total, used, usage_percent(used, fs.f_bavail * block))
}

pub fn disks() -> Vec<Disk> {
    let mut out: Vec<Disk> = Vec::new();
    unsafe {
        let n = libc::getfsstat(std::ptr::null_mut(), 0, libc::MNT_NOWAIT);
        if n <= 0 {
            return out;
        }
        let mut buf: Vec<libc::statfs> = Vec::with_capacity(n as usize);
        let got = libc::getfsstat(buf.as_mut_ptr(), n * size_of::<libc::statfs>() as libc::c_int, libc::MNT_NOWAIT);
        if got <= 0 {
            return out;
        }
        buf.set_len(got.min(n) as usize);
        // "/" is a sealed read-only snapshot; what the user owns lives in the Data volume of
        // the same APFS group. Reporting "/" verbatim shows 11G used on a disk that is full.
        let mut data: libc::statfs = zeroed();
        let have_data = libc::statfs(DATA_VOLUME.as_ptr() as *const libc::c_char, &mut data) == 0;
        for fs in &buf {
            let fstype = CStr::from_ptr(fs.f_fstypename.as_ptr()).to_string_lossy();
            // Finder hides nobrowse volumes (Preboot, VM, Update, ...) and so do we.
            if SKIP_FS.contains(&fstype.as_ref()) || fs.f_flags & libc::MNT_DONTBROWSE as u32 != 0 {
                continue;
            }
            let mount = CStr::from_ptr(fs.f_mntonname.as_ptr()).to_string_lossy().into_owned();
            if out.iter().any(|d| d.mount == mount) {
                continue;
            }
            let (total, used, percent) = usage_of(if mount == "/" && have_data { &data } else { fs });
            out.push(Disk { mount, total, used, percent });
        }
    }
    out
}

/// Bytes sent and received by all interfaces. `buf` is kept by the caller between reads:
/// the list is fetched with ONE sysctl into it, and only when it has grown is the size
/// asked for again.
pub fn net(buf: &mut Vec<u8>) -> Net {
    let mut total = Net::default();
    let mut mib = [libc::CTL_NET, libc::PF_ROUTE, 0, 0, libc::NET_RT_IFLIST2, 0];
    unsafe {
        let mut len: libc::size_t = buf.len();
        let fits = len > 0
            && libc::sysctl(mib.as_mut_ptr(), 6, buf.as_mut_ptr() as *mut libc::c_void, &mut len, std::ptr::null_mut(), 0) == 0;
        if !fits {
            let mut need: libc::size_t = 0;
            if libc::sysctl(mib.as_mut_ptr(), 6, std::ptr::null_mut(), &mut need, std::ptr::null_mut(), 0) != 0 || need == 0 {
                return total;
            }
            buf.resize(need + 4096, 0);
            len = buf.len();
            if libc::sysctl(mib.as_mut_ptr(), 6, buf.as_mut_ptr() as *mut libc::c_void, &mut len, std::ptr::null_mut(), 0) != 0 {
                return total;
            }
        }
        let mut at = 0usize;
        while at + size_of::<libc::if_msghdr>() <= len {
            let head = std::ptr::read_unaligned(buf.as_ptr().add(at) as *const libc::if_msghdr);
            let step = head.ifm_msglen as usize;
            if step == 0 {
                break;
            }
            if head.ifm_type as libc::c_int == libc::RTM_IFINFO2 && at + size_of::<libc::if_msghdr2>() <= len {
                let msg = std::ptr::read_unaligned(buf.as_ptr().add(at) as *const libc::if_msghdr2);
                total.sent += msg.ifm_data.ifi_obytes;
                total.recv += msg.ifm_data.ifi_ibytes;
            }
            at += step;
        }
    }
    total
}

// --- disk throughput: the one number macOS only hands out through IOKit --------------------

type CfRef = *const libc::c_void;
const UTF8: u32 = 0x0800_0100;
const CF_NUMBER_SINT64: libc::c_int = 4;

#[link(name = "IOKit", kind = "framework")]
extern "C" {
    fn IOServiceMatching(name: *const libc::c_char) -> CfRef;
    fn IOServiceGetMatchingServices(port: u32, matching: CfRef, iterator: *mut u32) -> libc::c_int;
    fn IOIteratorNext(iterator: u32) -> u32;
    fn IOObjectRelease(object: u32) -> libc::c_int;
    fn IORegistryEntryCreateCFProperty(entry: u32, key: CfRef, allocator: CfRef, options: u32) -> CfRef;
}

extern "C" {
    // libnotify, part of libSystem: the kernel's thermal pressure level without a framework.
    fn notify_register_check(name: *const libc::c_char, token: *mut libc::c_int) -> u32;
    fn notify_get_state(token: libc::c_int, state: *mut u64) -> u32;
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFStringCreateWithCString(allocator: CfRef, text: *const libc::c_char, encoding: u32) -> CfRef;
    fn CFDictionaryGetValue(dict: CfRef, key: CfRef) -> CfRef;
    fn CFNumberGetValue(number: CfRef, kind: libc::c_int, value: *mut libc::c_void) -> u8;
    fn CFBooleanGetValue(boolean: CfRef) -> u8;
    fn CFRelease(object: CfRef);
}

struct IoKeys {
    statistics: usize,
    read: usize,
    written: usize,
}

fn io_keys() -> &'static IoKeys {
    // CFStrings made once and kept for the life of the process.
    static KEYS: OnceLock<IoKeys> = OnceLock::new();
    KEYS.get_or_init(|| unsafe {
        let make = |text: &[u8]| CFStringCreateWithCString(std::ptr::null(), text.as_ptr() as *const libc::c_char, UTF8) as usize;
        IoKeys { statistics: make(b"Statistics\0"), read: make(b"Bytes (Read)\0"), written: make(b"Bytes (Write)\0") }
    })
}

/// Bytes read and written by every block storage driver since boot.
pub fn disk_io() -> Option<(u64, u64)> {
    let keys = io_keys();
    let (mut read, mut written, mut any) = (0u64, 0u64, false);
    unsafe {
        let matching = IOServiceMatching(b"IOBlockStorageDriver\0".as_ptr() as *const libc::c_char);
        if matching.is_null() {
            return None;
        }
        let mut iterator = 0u32;
        // The call consumes `matching`; port 0 is the default main port.
        if IOServiceGetMatchingServices(0, matching, &mut iterator) != 0 {
            return None;
        }
        loop {
            let drive = IOIteratorNext(iterator);
            if drive == 0 {
                break;
            }
            let stats = IORegistryEntryCreateCFProperty(drive, keys.statistics as CfRef, std::ptr::null(), 0);
            if !stats.is_null() {
                for (key, total) in [(keys.read, &mut read), (keys.written, &mut written)] {
                    let number = CFDictionaryGetValue(stats, key as CfRef);
                    let mut value = 0i64;
                    if !number.is_null() && CFNumberGetValue(number, CF_NUMBER_SINT64, &mut value as *mut i64 as *mut libc::c_void) != 0 {
                        *total += value.max(0) as u64;
                        any = true;
                    }
                }
                CFRelease(stats);
            }
            IOObjectRelease(drive);
        }
        IOObjectRelease(iterator);
    }
    if any { Some((read, written)) } else { None }
}

pub fn boot_time() -> f64 {
    let mut mib = [libc::CTL_KERN, libc::KERN_BOOTTIME];
    unsafe {
        let mut tv: libc::timeval = zeroed();
        let mut len = size_of::<libc::timeval>();
        if libc::sysctl(mib.as_mut_ptr(), 2, &mut tv as *mut _ as *mut libc::c_void, &mut len, std::ptr::null_mut(), 0) == 0 {
            return tv.tv_sec as f64 + tv.tv_usec as f64 / 1e6;
        }
    }
    0.0
}

#[allow(deprecated)]
fn timebase() -> (u64, u64) {
    static TB: OnceLock<(u64, u64)> = OnceLock::new();
    *TB.get_or_init(|| unsafe {
        let mut tb: libc::mach_timebase_info = zeroed();
        libc::mach_timebase_info(&mut tb);
        (tb.numer.max(1) as u64, tb.denom.max(1) as u64)
    })
}

/// Every process whose task info this user may read; returns how many processes exist.
pub fn proc_scan(pids: &mut Vec<i32>, out: &mut Vec<ProcSample>) -> usize {
    out.clear();
    let (numer, denom) = timebase();
    unsafe {
        let want = libc::proc_listallpids(std::ptr::null_mut(), 0);
        if want <= 0 {
            return 0;
        }
        pids.clear();
        pids.resize(want as usize + 64, 0);
        let n = libc::proc_listallpids(pids.as_mut_ptr() as *mut libc::c_void,
                                       (pids.len() * size_of::<i32>()) as libc::c_int);
        if n <= 0 {
            return 0;
        }
        pids.truncate(n as usize);
        let size = size_of::<libc::proc_taskinfo>() as libc::c_int;
        for &pid in pids.iter() {
            if pid <= 0 {
                continue;
            }
            let mut ti: libc::proc_taskinfo = zeroed();
            if libc::proc_pidinfo(pid, libc::PROC_PIDTASKINFO, 0, &mut ti as *mut _ as *mut libc::c_void, size) == size {
                let ticks = ti.pti_total_user + ti.pti_total_system;
                out.push(ProcSample { pid, cpu_ns: (ticks as u128 * numer as u128 / denom as u128) as u64,
                                      rss: ti.pti_resident_size });
            }
        }
        pids.len()
    }
}

/// The program a path belongs to. Usually the file name - but some programs install each
/// version under its number (".../claude/versions/2.1.325"), and a row that reads "2.1.325"
/// names nothing: then the nearest directory that is a name, not a number or a layout word.
pub fn name_from_path(path: &str) -> Option<String> {
    const LAYOUT: [&str; 6] = ["versions", "version", "bin", "current", "Contents", "MacOS"];
    let mut parts = path.rsplit('/').filter(|p| !p.is_empty());
    let file = parts.next()?;
    if file.chars().any(|c| c.is_alphabetic()) {
        return Some(file.to_string());
    }
    parts.find(|p| p.chars().any(|c| c.is_alphabetic()) && !LAYOUT.contains(p)).or(Some(file)).map(str::to_string)
}

/// The executable's name - asked for the handful of rows that are drawn, never for all.
pub fn proc_name(pid: i32) -> String {
    let mut buf = [0u8; 4096];
    unsafe {
        let n = libc::proc_pidpath(pid, buf.as_mut_ptr() as *mut libc::c_void, buf.len() as u32);
        if n > 0 {
            let path = String::from_utf8_lossy(&buf[..n as usize]);
            if let Some(name) = name_from_path(&path) {
                return name;
            }
        }
        let n = libc::proc_name(pid, buf.as_mut_ptr() as *mut libc::c_void, 64);
        if n > 0 {
            return String::from_utf8_lossy(&buf[..n as usize]).into_owned();
        }
    }
    "?".to_string()
}

// --- what the recorder reads besides the dashboard's numbers --------------------------------

/// Call `visit` with every registered IOKit service of `class` (NUL-terminated).
fn each_service(class: &[u8], mut visit: impl FnMut(u32)) {
    unsafe {
        let matching = IOServiceMatching(class.as_ptr() as *const libc::c_char);
        if matching.is_null() {
            return;
        }
        let mut iterator = 0u32;
        if IOServiceGetMatchingServices(0, matching, &mut iterator) != 0 {
            return;
        }
        loop {
            let service = IOIteratorNext(iterator);
            if service == 0 {
                break;
            }
            visit(service);
            IOObjectRelease(service);
        }
        IOObjectRelease(iterator);
    }
}

/// A registry property of a service; the caller releases it. `name` is NUL-terminated.
unsafe fn property(service: u32, name: &[u8]) -> CfRef {
    let key = CFStringCreateWithCString(std::ptr::null(), name.as_ptr() as *const libc::c_char, UTF8);
    if key.is_null() {
        return std::ptr::null();
    }
    let value = IORegistryEntryCreateCFProperty(service, key, std::ptr::null(), 0);
    CFRelease(key);
    value
}

unsafe fn number(value: CfRef) -> Option<i64> {
    let mut out = 0i64;
    if !value.is_null() && CFNumberGetValue(value, CF_NUMBER_SINT64, &mut out as *mut i64 as *mut libc::c_void) != 0 {
        Some(out)
    } else {
        None
    }
}

fn gpu_utilisation() -> Option<f64> {
    let mut busiest: Option<f64> = None;
    each_service(b"IOAccelerator\0", |service| unsafe {
        let stats = property(service, b"PerformanceStatistics\0");
        if stats.is_null() {
            return;
        }
        let key = CFStringCreateWithCString(std::ptr::null(), b"Device Utilization %\0".as_ptr() as *const libc::c_char, UTF8);
        if let Some(pct) = number(CFDictionaryGetValue(stats, key)) {
            let pct = (pct as f64).clamp(0.0, 100.0);
            busiest = Some(busiest.map_or(pct, |b: f64| b.max(pct)));
        }
        CFRelease(key);
        CFRelease(stats);
    });
    busiest
}

fn power() -> (Option<bool>, Option<u8>) {
    let mut found = (None, None);
    each_service(b"AppleSmartBattery\0", |service| unsafe {
        let external = property(service, b"ExternalConnected\0");
        if !external.is_null() {
            found.0 = Some(CFBooleanGetValue(external) != 0);
            CFRelease(external);
        }
        let (now, full) = (property(service, b"CurrentCapacity\0"), property(service, b"MaxCapacity\0"));
        if let (Some(now_v), Some(full_v)) = (number(now), number(full)) {
            if full_v > 0 {
                found.1 = Some((now_v * 100 / full_v).clamp(0, 100) as u8);
            }
        }
        for value in [now, full] {
            if !value.is_null() {
                CFRelease(value);
            }
        }
    });
    found
}

/// Displays beyond the built-in one. A port with nothing plugged in is listed as external
/// too, but only a connected display carries attributes.
fn external_displays() -> Option<u8> {
    let (mut ports, mut connected) = (0u32, 0u8);
    each_service(b"AppleCLCD2\0", |service| unsafe {
        ports += 1;
        let external = property(service, b"external\0");
        let attributes = property(service, b"DisplayAttributes\0");
        if !external.is_null() && CFBooleanGetValue(external) != 0 && !attributes.is_null() {
            connected += 1;
        }
        for value in [external, attributes] {
            if !value.is_null() {
                CFRelease(value);
            }
        }
    });
    if ports == 0 { None } else { Some(connected) }
}

fn thermal_level() -> Option<u8> {
    static TOKEN: OnceLock<Option<libc::c_int>> = OnceLock::new();
    let token = (*TOKEN.get_or_init(|| unsafe {
        let mut token: libc::c_int = 0;
        let name = b"com.apple.system.thermalpressurelevel\0";
        if notify_register_check(name.as_ptr() as *const libc::c_char, &mut token) == 0 { Some(token) } else { None }
    }))?;
    let mut state = 0u64;
    if unsafe { notify_get_state(token, &mut state) } == 0 { Some(state.min(9) as u8) } else { None }
}

/// `full` also asks for the power source and the displays - two registry walks that change
/// rarely, so the recorder asks for them only every few samples.
pub fn vitals(full: bool) -> Vitals {
    // The kernel's own scale is 1 normal, 2 warning, 4 critical.
    let pressure = sysctl_value::<libc::c_int>(b"kern.memorystatus_vm_pressure_level\0").map(|level| match level {
        4.. => 2,
        2 | 3 => 1,
        _ => 0,
    });
    let (on_ac, battery_pct) = if full { power() } else { (None, None) };
    let ext_displays = if full { external_displays() } else { None };
    Vitals { gpu_pct: gpu_utilisation(), pressure, thermal: thermal_level(), on_ac, battery_pct, ext_displays }
}

fn sysctl_string(name: &[u8]) -> String {
    let mut buf = [0u8; 256];
    let mut len = buf.len();
    let rc = unsafe {
        libc::sysctlbyname(name.as_ptr() as *const libc::c_char, buf.as_mut_ptr() as *mut libc::c_void, &mut len, std::ptr::null_mut(), 0)
    };
    if rc != 0 {
        return String::new();
    }
    let end = buf[..len.min(buf.len())].iter().position(|&b| b == 0).unwrap_or(len.min(buf.len()));
    String::from_utf8_lossy(&buf[..end]).trim().to_string()
}

/// (model identifier, chip name), e.g. ("MacBookPro18,1", "Apple M1 Pro").
pub fn machine() -> (String, String) {
    (sysctl_string(b"hw.model\0"), sysctl_string(b"machdep.cpu.brand_string\0"))
}
