# Tailwind CSS Zinc, matching Foot. Semantic colors use normal 400 / bright 500.
# Syntax errors and cancellation use bright red; other states use normal colors.
status is-interactive; or return

set -g fish_color_normal d4d4d8
set -g fish_color_command fafafa
set -g fish_color_keyword e4e4e7 --bold
set -g fish_color_quote e4e4e7
set -g fish_color_redirection a1a1aa --bold
set -g fish_color_end a1a1aa
set -g fish_color_error ef4444
set -g fish_color_param d4d4d8
set -g fish_color_valid_path d4d4d8 --underline
set -g fish_color_option a1a1aa
set -g fish_color_comment 71717a
set -g fish_color_selection fafafa --background=3f3f46
set -g fish_color_operator e4e4e7
set -g fish_color_escape a1a1aa
set -g fish_color_autosuggestion 71717a
set -g fish_color_search_match fafafa --background=3f3f46 --bold
set -g fish_color_history_current fafafa --bold
set -g fish_color_cwd fafafa
set -g fish_color_cwd_root f87171
set -g fish_color_user d4d4d8
set -g fish_color_host a1a1aa
set -g fish_color_host_remote fbbf24
set -g fish_color_status f87171
set -g fish_color_cancel ef4444 --reverse

set -g fish_pager_color_progress fafafa --background=3f3f46
set -g fish_pager_color_background --background=09090b
set -g fish_pager_color_prefix e4e4e7 --bold
set -g fish_pager_color_completion d4d4d8
set -g fish_pager_color_description a1a1aa
set -g fish_pager_color_secondary_background --background=18181b
set -g fish_pager_color_secondary_prefix e4e4e7 --bold
set -g fish_pager_color_secondary_completion d4d4d8
set -g fish_pager_color_secondary_description a1a1aa
set -g fish_pager_color_selected_background --background=3f3f46
set -g fish_pager_color_selected_prefix fafafa --bold
set -g fish_pager_color_selected_completion fafafa
set -g fish_pager_color_selected_description e4e4e7

set -g __fish_git_prompt_color a1a1aa
set -g __fish_git_prompt_color_prefix 71717a
set -g __fish_git_prompt_color_suffix 71717a
set -g __fish_git_prompt_color_branch d4d4d8
set -g __fish_git_prompt_color_branch_detached fbbf24
set -g __fish_git_prompt_color_branch_dirty fbbf24
set -g __fish_git_prompt_color_branch_staged 4ade80
set -g __fish_git_prompt_color_bare a1a1aa
set -g __fish_git_prompt_color_merging fbbf24
set -g __fish_git_prompt_color_cleanstate 4ade80
set -g __fish_git_prompt_color_invalidstate ef4444
set -g __fish_git_prompt_color_flags fbbf24
set -g __fish_git_prompt_color_dirtystate fbbf24
set -g __fish_git_prompt_color_stagedstate 4ade80
set -g __fish_git_prompt_color_stashstate 60a5fa
set -g __fish_git_prompt_color_untrackedfiles fbbf24
set -g __fish_git_prompt_color_upstream 60a5fa
