use std::path::Path;

pub fn display_path(path: &str, home: Option<&Path>) -> String {
    compact_home(path, home)
}

pub fn compact_home(path: &str, home: Option<&Path>) -> String {
    if let Some(relative) = home.and_then(|home| Path::new(path).strip_prefix(home).ok()) {
        if relative.as_os_str().is_empty() {
            "~/".to_string()
        } else {
            format!("~/{}", relative.display())
        }
    } else {
        path.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compacts_home_and_its_descendants() {
        let home = Some(Path::new("/home/user"));
        assert_eq!(display_path("/home/user/Downloads", home), "~/Downloads");
        assert_eq!(display_path("/home/user/Drives/Files/project/report.txt", home), "~/Drives/Files/project/report.txt");
        assert_eq!(display_path("/home/user/Drives/Files", home), "~/Drives/Files");
        assert_eq!(display_path("/home/user", home), "~/");
    }

    #[test]
    fn leaves_external_paths_and_similarly_named_homes_unchanged() {
        let home = Some(Path::new("/home/user"));
        for path in ["/run/media/user/Files/report.txt", "/home/user-other/report.txt", "/home/other/home/user"] {
            assert_eq!(display_path(path, home), path);
        }
        assert_eq!(display_path("/home/user/Downloads", None), "/home/user/Downloads");
    }
}
