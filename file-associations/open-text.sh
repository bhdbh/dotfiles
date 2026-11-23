#!/usr/bin/env bash
# Share the extension rule between XDG/Anyrun and Yazi's current terminal.
set -euo pipefail

current_terminal=false
if [[ ${1-} == --terminal ]]; then
    current_terminal=true
    shift
fi

micro_files=()
code_files=()
for path do
    # Absolute paths also protect filenames beginning with a command-line flag.
    [[ $path == /* ]] || path=$PWD/$path
    case ${path,,} in
        *.txt) micro_files+=("$path") ;;
        *) code_files+=("$path") ;;
    esac
done

if (( ${#code_files[@]} )); then
    if (( ${#micro_files[@]} )); then
        code -- "${code_files[@]}" &
    else
        exec code -- "${code_files[@]}"
    fi
fi
if (( ${#micro_files[@]} )); then
    if $current_terminal; then
        exec micro "${micro_files[@]}"
    else
        exec foot --app-id=micro micro "${micro_files[@]}"
    fi
fi
