use std::{
    collections::HashSet,
    ffi::OsString,
    fs, io,
    os::unix::ffi::OsStringExt,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UsbMount {
    pub path: PathBuf,
    id: u64,
    device: String,
}

pub fn discover() -> io::Result<Vec<UsbMount>> {
    let table = fs::read_to_string("/proc/self/mountinfo")?;
    Ok(discover_from(&table, Path::new("/sys/dev/block")))
}

pub(crate) fn discover_from(table: &str, block_devices: &Path) -> Vec<UsbMount> {
    let mut mounts = Vec::new();
    for line in table.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() < 10 || !fields.contains(&"-") { continue }
        let Ok(id) = fields[0].parse::<u64>() else { continue };
        let mut numbers = fields[2].split(':');
        if numbers.next().and_then(|n| n.parse::<u32>().ok()).is_none()
            || numbers.next().and_then(|n| n.parse::<u32>().ok()).is_none()
            || numbers.next().is_some()
        { continue }
        let path = decode_path(fields[4]);
        if !path.is_absolute() || path == Path::new("/") { continue }
        let block_usb = is_usb_device(&block_devices.join(fields[2]), &mut HashSet::new(), 0);
        // FUSE filesystems can report a virtual st_dev while their source is
        // still the USB partition (for example, ntfs-3g).
        let source_usb = fields.iter().position(|field| *field == "-")
            .and_then(|separator| fields.get(separator + 2))
            .map(|source| decode_path(source))
            .filter(|source| source.starts_with("/dev"))
            .and_then(|source| {
                let canonical = fs::canonicalize(&source).unwrap_or(source);
                let name = canonical.file_name()?;
                let class = block_devices.parent()?.parent()?.join("class/block").join(name);
                Some(is_usb_device(&class, &mut HashSet::new(), 0))
            }).unwrap_or(false);
        if block_usb || source_usb {
            mounts.push(UsbMount { path, id, device: fields[2].to_owned() });
        }
    }
    mounts.sort_by(|left, right| left.path.cmp(&right.path).then(left.id.cmp(&right.id)));
    mounts.dedup();
    mounts
}

fn is_usb_device(device: &Path, visited: &mut HashSet<PathBuf>, depth: usize) -> bool {
    if depth > 16 { return false }
    let Ok(device) = fs::canonicalize(device) else { return false };
    if !visited.insert(device.clone()) { return false }
    if device.components().any(|component| {
        component.as_os_str().to_str().and_then(|name| name.strip_prefix("usb"))
            .is_some_and(|number| !number.is_empty() && number.bytes().all(|byte| byte.is_ascii_digit()))
    }) { return true }
    fs::read_dir(device.join("slaves")).ok().is_some_and(|slaves| {
        slaves.filter_map(Result::ok)
            .any(|slave| is_usb_device(&slave.path(), visited, depth + 1))
    })
}

fn decode_path(value: &str) -> PathBuf {
    let input = value.as_bytes();
    let mut output = Vec::new();
    let mut index = 0;
    while index < input.len() {
        if input[index] == b'\\' && index + 3 < input.len()
            && input[index + 1..=index + 3].iter().all(|byte| (b'0'..=b'7').contains(byte))
        {
            let number = (input[index + 1] - b'0') as u16 * 64
                + (input[index + 2] - b'0') as u16 * 8
                + (input[index + 3] - b'0') as u16;
            if number <= 255 {
                output.push(number as u8);
                index += 4;
                continue;
            }
        }
        output.push(input[index]);
        index += 1;
    }
    PathBuf::from(OsString::from_vec(output))
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    pub struct Fixture(pub PathBuf);
    impl Fixture {
        pub fn new() -> Self {
            let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
            let root = std::env::temp_dir().join(format!("kidex-usb-{}-{unique}", std::process::id()));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    pub(crate) fn block_fixture(root: &Path) -> PathBuf {
        let block = root.join("sys/dev/block");
        let usb = root.join("sys/devices/pci/usb3/3-1/block/sda/sda1");
        let internal = root.join("sys/devices/pci/nvme/block/nvme0n1/nvme0n1p3");
        let encrypted = root.join("sys/devices/virtual/block/dm-0");
        for path in [&block, &usb, &internal, &encrypted.join("slaves")] {
            fs::create_dir_all(path).unwrap();
        }
        symlink(&usb, block.join("8:1")).unwrap();
        symlink(&internal, block.join("259:3")).unwrap();
        symlink(&encrypted, block.join("253:0")).unwrap();
        symlink(&usb, encrypted.join("slaves/sda1")).unwrap();
        let class = root.join("sys/class/block");
        fs::create_dir_all(&class).unwrap();
        symlink(&usb, class.join("sda1")).unwrap();
        block
    }

    #[test]
    fn usb_partitions_and_encrypted_usb_are_detected_but_internal_disks_are_not() {
        let root = Fixture::new();
        let block = block_fixture(&root.0);
        let table = "10 1 8:1 / /media/USB\\040Files rw - exfat /dev/sda1 rw\n11 1 259:3 / /home rw - xfs /dev/nvme0n1p3 rw\n12 1 253:0 / /mnt/encrypted rw - ext4 /dev/dm-0 rw\n13 1 8:1 / / rw - exfat /dev/sda1 rw\n";
        let mounts = discover_from(table, &block);
        assert_eq!(mounts.iter().map(|mount| mount.path.clone()).collect::<Vec<_>>(), vec![PathBuf::from("/media/USB Files"), PathBuf::from("/mnt/encrypted")]);
    }

    #[test]
    fn adding_removing_and_remounting_are_distinct_snapshots() {
        let root = Fixture::new();
        let block = block_fixture(&root.0);
        let first = discover_from("10 1 8:1 / /mnt/usb rw - exfat /dev/sda1 rw", &block);
        assert_eq!(first.len(), 1);
        assert_ne!(first, discover_from("", &block));
        assert_ne!(first, discover_from("20 1 8:1 / /mnt/usb rw - exfat /dev/sda1 rw", &block));
        assert!(discover_from("broken input", &block).is_empty());
    }

    #[test]
    fn mount_path_escapes_preserve_spaces_tabs_and_backslashes() {
        assert_eq!(decode_path("/media/a\\040b\\011c\\134d"), PathBuf::from("/media/a b\tc\\d"));
    }

    #[test]
    fn fuse_mounts_use_the_underlying_usb_source() {
        let root = Fixture::new();
        let block = block_fixture(&root.0);
        let mounts = discover_from("10 1 0:99 / /mnt/ntfs rw - fuseblk /dev/sda1 rw", &block);
        assert_eq!(mounts.len(), 1);
        assert_eq!(mounts[0].path, PathBuf::from("/mnt/ntfs"));
    }
}
