/// Return the query for the selected plugin, or suppress it in an exclusive search.
pub fn plugin_query<'a>(text: &'a str, plugin_name: &str) -> Option<&'a str> {
    let trimmed = text.trim_start();
    if trimmed.is_empty() || text.trim() == "?" {
        return None;
    }
    if let Some(expression) = trimmed.strip_prefix('=') {
        return (plugin_name == "Rink" && !expression.trim().is_empty()).then_some(trimmed);
    }
    if let Some(query) = trimmed.strip_prefix('>') {
        return (plugin_name == "Applications").then(|| query.trim_start());
    }
    if trimmed.starts_with(['#', '@']) {
        // Kidex needs the prefix to filter the index before limiting its results.
        return (plugin_name == "Kidex").then_some(trimmed);
    }
    if trimmed.starts_with('$') {
        return (plugin_name == "Shell").then_some(trimmed);
    }
    if trimmed.starts_with(':') {
        // Actions handles the prefix so a bare ':' can list every action.
        return (plugin_name == "Actions").then_some(trimmed);
    }
    (plugin_name != "Rink").then_some(text)
}
