"""Customize initramfs prompt data without changing native authentication code."""
import argparse
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

PROMPT = b'Disk Password:%.0s%.0s\0'
ORIGINALS = (b'Please enter %s for disk %s:\0',
             b'Please enter %s for disk %s (verification):\0')


def sections(data):
    if data[:6] != b'\x7fELF\x02\x01':
        raise RuntimeError('Expected a little-endian ELF64 executable.')
    offset, = struct.unpack_from('<Q', data, 40)
    size, count, names_index = struct.unpack_from('<HHH', data, 58)
    if size != 64 or not count or names_index >= count or offset + size * count > len(data):
        raise RuntimeError('Unexpected ELF section table.')
    entries = [struct.unpack_from('<IIQQQQIIQQ', data, offset + index * size)
               for index in range(count)]
    names = entries[names_index]
    strings = data[names[4]:names[4] + names[5]]
    result = {}
    for entry in entries:
        name = strings[entry[0]:].split(b'\0', 1)[0].decode('ascii')
        result[name] = dict(flags=entry[2], offset=entry[4], size=entry[5])
    return result


def customize_binary(data):
    layout = sections(data)
    rodata = layout.get('.rodata')
    if not rodata or rodata['flags'] & 5:  # SHF_WRITE | SHF_EXECINSTR
        raise RuntimeError('Expected a non-executable, read-only string section.')
    start, end = rodata['offset'], rodata['offset'] + rodata['size']
    patched = bytearray(data)
    changed = 0
    for original in ORIGINALS:
        replacement = PROMPT.ljust(len(original), b'\0')
        if len(replacement) != len(original):
            raise RuntimeError('Replacement must fit in the original string slot.')
        if data.count(original) != 1:
            raise RuntimeError('The vendor prompt format changed; refusing to modify this binary.')
        offset = data.index(original)
        if offset < start or offset + len(original) > end:
            raise RuntimeError('Prompt was not entirely inside .rodata.')
        patched[offset:offset + len(original)] = replacement
        changed += 1
    if len(data) != len(patched):
        raise RuntimeError('ELF size changed unexpectedly.')
    for section in layout.values():
        if section['flags'] & 4:
            lo, hi = section['offset'], section['offset'] + section['size']
            if data[lo:hi] != patched[lo:hi]:
                raise RuntimeError('Executable code changed unexpectedly.')
    if changed != 2:
        raise RuntimeError('Expected exactly two prompt strings.')
    return bytes(patched)


def customize_crypttab(text, uuid):
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', uuid):
        raise RuntimeError('Invalid LUKS UUID.')
    updated, matches = [], 0
    for line in text.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith('#'):
            updated.append(line)
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ('UUID=' + uuid, '/dev/disk/by-uuid/' + uuid):
            updated.append(line)
            continue
        if len(parts) not in (3, 4) or parts[2] not in ('none', '-'):
            raise RuntimeError('Expected the existing passphrase-based crypttab entry.')
        options = parts[3].split(',') if len(parts) == 4 and parts[3] != 'none' else []
        options = [option for option in options if not option.startswith('password-echo=')]
        options.append('password-echo=no')
        # Keep the mapping name, source, key field and every other option.
        updated.append('\t'.join([*parts[:3], ','.join(options)]) + '\n')
        matches += 1
    if matches != 1:
        raise RuntimeError('Expected exactly one root-volume crypttab entry.')
    return ''.join(updated)


def replace(path, data):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Expected a regular initramfs file: ' + str(path))
    details = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.disk-prompt-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            os.fchown(output.fileno(), details.st_uid, details.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(details.st_mode))
        # Always replace the initramfs copy, even when dracut used hardlinks.
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--crypttab', required=True, type=Path)
    parser.add_argument('--uuid', required=True)
    args = parser.parse_args()
    binary = customize_binary(args.binary.read_bytes())
    crypttab = customize_crypttab(args.crypttab.read_text(), args.uuid)
    replace(args.binary, binary)
    replace(args.crypttab, crypttab.encode())
    print('Disk prompt customized: Disk Password: (input hidden).')


if __name__ == '__main__':
    main()
