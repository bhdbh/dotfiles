pub fn expression(text: &str) -> Option<&str> {
    text.trim_start().strip_prefix('=').map(str::trim)
}

pub fn result_text(text: &str, result: &str) -> String {
    if expression(text).is_none() { return format!("= {result}"); }
    let prefix_end = text.find('=').unwrap() + 1;
    let rest = &text[prefix_end..];
    let spacing_end = rest.find(|c: char| !c.is_whitespace()).unwrap_or(rest.len());
    format!("{}{result}", &text[..prefix_end + spacing_end])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replaces_the_expression_and_preserves_prefix_spacing() {
        assert_eq!(result_text("=2+2", "4"), "=4");
        assert_eq!(result_text("= 2+2", "4"), "= 4");
        assert_eq!(result_text("  =   2+2", "4"), "  =   4");
        assert_eq!(result_text("= 2 m + 3 m", "5 meter"), "= 5 meter");
        assert_eq!(expression(&format!("{} * 3", result_text("=2+2", "4"))), Some("4 * 3"));
    }

    #[test]
    fn detects_empty_prefixes_without_treating_normal_search_as_calculation() {
        assert_eq!(expression("=  "), Some(""));
        assert_eq!(expression("2+2"), None);
        assert_eq!(expression("file=name"), None);
        assert_eq!(result_text("report", "4"), "= 4");
    }
}
