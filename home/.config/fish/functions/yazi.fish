function yazi --description "Yazi with directory synchronization"
    set -l tmp (mktemp -t "yazi-cwd.XXXXXX")

    command yazi $argv --cwd-file="$tmp"
    set -l result $status

    if test -s "$tmp"
        set -l cwd (string trim -- (cat "$tmp"))

        if test -d "$cwd"; and test "$cwd" != "$PWD"
            builtin cd -- "$cwd"
        end
    end

    command rm -f -- "$tmp"
    return $result
end
