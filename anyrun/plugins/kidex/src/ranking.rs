use std::{cmp::Reverse, path::Path};
use fuzzy_matcher::{skim::SkimMatcherV2, FuzzyMatcher};

#[derive(Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Rank {
    quality: u8,
    nearby: usize,
    score: Reverse<i64>,
    length: usize,
}

pub fn rank(path: &Path, query: &str, matcher: &SkimMatcherV2) -> Option<Rank> {
    let full = path.to_string_lossy();
    let full_score = matcher.fuzzy_match(&full, query)?;
    let length = full.len();
    if query.contains('/') {
        return Some(Rank { quality: 0, nearby: 0, score: Reverse(full_score), length });
    }
    let name = path.file_name()?.to_string_lossy();
    let name_score = matcher.fuzzy_match(&name, query);
    let normalized = name.to_lowercase();
    let query = query.to_lowercase();
    let quality = if name_score.is_none() { 3 }
        else if normalized.starts_with(&query) { 0 }
        else if normalized.contains(&query) { 1 }
        else { 2 };
    Some(Rank { quality, nearby: length, score: Reverse(name_score.unwrap_or(full_score)), length })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sorted<'a>(query: &str, paths: &[&'a str]) -> Vec<&'a str> {
        let matcher = SkimMatcherV2::default().smart_case();
        let mut ranked: Vec<_> = paths.iter().filter_map(|p| rank(Path::new(p), query, &matcher).map(|r| (*p, r))).collect();
        ranked.sort_by(|a, b| a.1.cmp(&b.1).then(a.0.cmp(b.0)));
        ranked.into_iter().map(|(path, _)| path).collect()
    }

    #[test]
    fn nearby_projects_beats_longer_drive_paths_and_ancestor_matches() {
        let paths = ["/home/user/Drives/Files/backup/project", "/home/user/Drives/Files/Projects/child", "/home/user/Projects"];
        assert_eq!(sorted("project", &paths)[0], "/home/user/Projects");
    }

    #[test]
    fn filename_matches_take_precedence_over_matching_only_a_parent() {
        assert_eq!(sorted("report", &["/home/user/report/x", "/home/user/Documents/report.txt"])[0], "/home/user/Documents/report.txt");
    }

    #[test]
    fn shorter_paths_win_within_the_same_kind_of_name_match() {
        assert_eq!(sorted("project", &["/home/user/Drives/Files/Projects", "/home/user/Projects"])[0], "/home/user/Projects");
    }

    #[test]
    fn explicit_paths_keep_full_path_scoring() {
        assert_eq!(sorted("/home/user/Drives/Files/Projects", &["/home/user/Projects", "/home/user/Drives/Files/Projects"]), vec!["/home/user/Drives/Files/Projects"]);
    }

    #[test]
    fn case_sensitive_queries_still_respect_smart_case() {
        assert!(sorted("PROJECT", &["/home/user/Projects"]).is_empty());
    }
}
