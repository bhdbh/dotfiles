if status is-interactive
# Commands to run in interactive sessions can go here

# Fish configuration
set -g fish_greeting

if status is-login
    and test (tty) = /dev/tty1
    and not set -q WAYLAND_DISPLAY
    and not set -q DISPLAY
    set -gx XDG_CURRENT_DESKTOP sway
    exec @HOME@/.local/bin/sway-native
end

if status is-interactive
    zoxide init fish | source
    fish_config theme choose zinc
end

end
