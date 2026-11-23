#!/usr/bin/env bash
# Install terminal/web fallback fonts for the current user, without sudo.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi

for command in curl tar sha256sum dnf rpm rpmkeys rpm2cpio cpio fc-match fc-cache; do
    command -v "$command" >/dev/null || {
        printf 'Comando necessário não encontrado: %s\n' "$command" >&2
        exit 1
    }
done

font_dir=${XDG_DATA_HOME:-$HOME/.local/share}/fonts/dotfiles-fallbacks
work_dir=$(mktemp -d -t dotfiles-fonts.XXXXXXXX)
trap 'rm -rf -- "$work_dir"' EXIT
mkdir -p -- "$font_dir"

# Official Nerd Fonts symbols: preserve Red Hat Mono and supply missing icons.
nerd_version=3.5.1
nerd_checksum=01172f37db8543edb102e5cb5c64101c9f4686630804d49b419aa07b23a69996
nerd_dir=$font_dir/nerd-symbols
if [[ ! -f $nerd_dir/SymbolsNerdFontMono-Regular.ttf \
    || ! -f $nerd_dir/SymbolsNerdFont-Regular.ttf \
    || $(cat "$nerd_dir/version" 2>/dev/null || true) != "$nerd_version" ]]; then
    archive=$work_dir/NerdFontsSymbolsOnly.tar.xz
    curl --fail --location --silent --show-error --retry 3 \
        "https://github.com/ryanoasis/nerd-fonts/releases/download/v$nerd_version/NerdFontsSymbolsOnly.tar.xz" \
        --output "$archive"
    printf '%s  %s\n' "$nerd_checksum" "$archive" | sha256sum --check --status
    mkdir -p -- "$work_dir/nerd" "$nerd_dir"
    tar --extract --xz --file "$archive" --directory "$work_dir/nerd" --no-same-owner
    install -m 644 "$work_dir/nerd/SymbolsNerdFontMono-Regular.ttf" "$nerd_dir/"
    install -m 644 "$work_dir/nerd/SymbolsNerdFont-Regular.ttf" "$nerd_dir/"
    install -m 644 "$work_dir/nerd/LICENSE" "$nerd_dir/"
    printf '%s\n' "$nerd_version" > "$nerd_dir/version"
fi

# Download Fedora font packages and extract only fonts/licenses into user data.
# Existing families (including Noto Color Emoji) are reused.
font_specs=(
    'google-noto-sans-vf-fonts|Noto Sans'
    'google-noto-serif-vf-fonts|Noto Serif'
    'google-noto-sans-mono-vf-fonts|Noto Sans Mono'
    'google-noto-color-emoji-fonts|Noto Color Emoji'
    'dejavu-sans-fonts|DejaVu Sans'
    'dejavu-serif-fonts|DejaVu Serif'
    'dejavu-sans-mono-fonts|DejaVu Sans Mono'
    'google-noto-sans-symbols-vf-fonts|Noto Sans Symbols'
    'google-noto-sans-symbols-2-fonts|Noto Sans Symbols 2'
    'google-noto-sans-math-fonts|Noto Sans Math'
    'google-noto-sans-cjk-vf-fonts|Noto Sans CJK JP'
    'google-noto-sans-mono-cjk-vf-fonts|Noto Sans Mono CJK JP'
    'google-noto-sans-arabic-vf-fonts|Noto Sans Arabic'
    'google-noto-sans-hebrew-vf-fonts|Noto Sans Hebrew'
    'google-noto-sans-devanagari-vf-fonts|Noto Sans Devanagari'
    'google-noto-sans-bengali-vf-fonts|Noto Sans Bengali'
    'google-noto-sans-tamil-vf-fonts|Noto Sans Tamil'
    'google-noto-sans-telugu-vf-fonts|Noto Sans Telugu'
    'google-noto-sans-kannada-vf-fonts|Noto Sans Kannada'
    'google-noto-sans-malayalam-vf-fonts|Noto Sans Malayalam'
    'google-noto-sans-gujarati-vf-fonts|Noto Sans Gujarati'
    'google-noto-sans-gurmukhi-vf-fonts|Noto Sans Gurmukhi'
    'google-noto-sans-sinhala-vf-fonts|Noto Sans Sinhala'
    'google-noto-sans-thai-vf-fonts|Noto Sans Thai'
    'google-noto-sans-khmer-vf-fonts|Noto Sans Khmer'
    'google-noto-sans-lao-vf-fonts|Noto Sans Lao'
    'google-noto-sans-myanmar-vf-fonts|Noto Sans Myanmar'
    'google-noto-sans-ethiopic-vf-fonts|Noto Sans Ethiopic'
    'google-noto-sans-armenian-vf-fonts|Noto Sans Armenian'
    'google-noto-sans-georgian-vf-fonts|Noto Sans Georgian'
)
packages=()
for spec in "${font_specs[@]}"; do
    package=${spec%%|*}
    family=${spec#*|}
    if [[ $(fc-match --format '%{family[0]}' "$family") != "$family" ]]; then
        packages+=("$package")
    fi
done

if (( ${#packages[@]} )); then
    mkdir -p -- "$work_dir/rpms"
    dnf download --destdir "$work_dir/rpms" "${packages[@]}"
    for archive in "$work_dir/rpms/"*.rpm; do
        rpmkeys --checksig "$archive"
        package=$(rpm -qp --queryformat '%{NAME}' "$archive")
        unpack_dir=$work_dir/$package
        target_dir=$font_dir/$package
        mkdir -p -- "$unpack_dir" "$target_dir/fonts" "$target_dir/licenses"
        (
            cd -- "$unpack_dir"
            rpm2cpio "$archive" | cpio --extract --make-directories --quiet --no-absolute-filenames \
                './usr/share/fonts/*' './usr/share/licenses/*'
        )
        while IFS= read -r -d '' font_file; do
            install -m 644 "$font_file" "$target_dir/fonts/"
        done < <(find "$unpack_dir/usr/share/fonts" -type f \
            \( -iname '*.ttf' -o -iname '*.otf' -o -iname '*.ttc' \) -print0)
        if [[ -d $unpack_dir/usr/share/licenses ]]; then
            cp -a -- "$unpack_dir/usr/share/licenses/." "$target_dir/licenses/"
        fi
        rpm -qp --queryformat '%{NAME}-%{VERSION}-%{RELEASE}\n' "$archive" > "$target_dir/installed-from"
    done
fi

fc-cache -f "$font_dir"
for family in 'Symbols Nerd Font Mono' 'Symbols Nerd Font'; do
    if [[ $(fc-match --format '%{family[0]}' "$family") != "$family" ]]; then
        printf 'Fonte não encontrada após a instalação: %s\n' "$family" >&2
        exit 1
    fi
done
for spec in "${font_specs[@]}"; do
    family=${spec#*|}
    if [[ $(fc-match --format '%{family[0]}' "$family") != "$family" ]]; then
        printf 'Fonte não encontrada após a instalação: %s\n' "$family" >&2
        exit 1
    fi
done
printf 'Fontes Nerd, emojis, símbolos e cobertura multilíngue disponíveis para terminal e web.\n'
