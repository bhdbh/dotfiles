use std::{fs, os::unix::fs::MetadataExt, path::Path, time::SystemTime};
use ignore::gitignore::{Gitignore, GitignoreBuilder};

pub fn load(root: &Path, file: Option<&Path>) -> Gitignore {
    let mut builder = GitignoreBuilder::new(root);
    if let Some(file) = file {
        if file.exists() {
            if let Some(why) = builder.add(file) { log::error!("Invalid search ignore rules: {why}"); }
        }
    }
    builder.build().unwrap_or_else(|why| {
        log::error!("Failed to compile search ignore rules: {why}");
        Gitignore::empty()
    })
}

pub fn fingerprint(file: Option<&Path>) -> Option<(SystemTime, u64, u64)> {
    let metadata = fs::metadata(file?).ok()?;
    Some((metadata.modified().ok()?, metadata.len(), metadata.ino()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mounts::tests::Fixture;

    #[test]
    fn supports_directory_patterns_globs_comments_and_exceptions() {
        let fixture = Fixture::new();
        let file = fixture.0.join("search.ignore");
        fs::write(&file, "# comments\nnode_modules/\n*.log\n!important.log\n/root-only/\n").unwrap();
        let rules = load(&fixture.0, Some(&file));
        assert!(rules.matched(fixture.0.join("project/node_modules"), true).is_ignore());
        assert!(!rules.matched(fixture.0.join("project/node_modules"), false).is_ignore());
        assert!(rules.matched(fixture.0.join("project/service.log"), false).is_ignore());
        assert!(!rules.matched(fixture.0.join("project/important.log"), false).is_ignore());
        assert!(rules.matched(fixture.0.join("root-only"), true).is_ignore());
        assert!(!rules.matched(fixture.0.join("project/root-only"), true).is_ignore());
    }

    #[test]
    fn reload_fingerprint_detects_edits_and_removal() {
        let fixture = Fixture::new();
        let file = fixture.0.join("search.ignore");
        assert_eq!(fingerprint(Some(&file)), None);
        fs::write(&file, "cache/\n").unwrap();
        let initial = fingerprint(Some(&file));
        fs::write(&file, "cache/\nlogs/\n").unwrap();
        assert_ne!(fingerprint(Some(&file)), initial);
        fs::remove_file(&file).unwrap();
        assert_eq!(fingerprint(Some(&file)), None);
    }
}
