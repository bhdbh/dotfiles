set -gx EDITOR "code --wait"
set -gx VISUAL "code --wait"

if status is-interactive; and type -q zoxide
    zoxide init fish | source
end
