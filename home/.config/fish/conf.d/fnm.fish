if test -x "$HOME/.local/bin/fnm"
    fish_add_path --path "$HOME/.local/bin"
    "$HOME/.local/bin/fnm" env --use-on-cd --version-file-strategy=recursive --shell fish | source
end
