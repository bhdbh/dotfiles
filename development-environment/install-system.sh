#!/usr/bin/env bash
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
(( EUID == 0 )) || { printf 'Execute com sudo.\n' >&2; exit 1; }

state_dir=/var/lib/local-development
developer_account=${SUDO_USER:?Execute este script via sudo.}
[[ $developer_account != root && $developer_account =~ ^[a-z_][a-z0-9_]*$ ]]
developer_home=$(getent passwd "$developer_account" | cut -d: -f6)
developer_group=$(id -gn "$developer_account")
export DEVELOPER_ACCOUNT="$developer_account" DEVELOPER_HOME="$developer_home"
credentials="$developer_home/.config/local-development/postgresql.env"
if [[ -f /var/lib/pgsql/data/PG_VERSION && ! -f $state_dir/postgres-prepared ]]; then
    printf 'Foi encontrado um banco existente. Revisão necessária antes de configurar.\n' >&2
    exit 1
fi
if [[ -e $credentials && ! -f $state_dir/postgres-prepared ]]; then
    printf 'Já existe um arquivo de credenciais. Nenhuma credencial será sobrescrita.\n' >&2
    exit 1
fi

install -d -m 0700 "$state_dir"
exec > >(tee -a "$state_dir/install.log") 2>&1
trap 'printf "Falha na instalação administrativa, linha %s.\n" "$LINENO"' ERR
rpm -qa > "$state_dir/packages-before.txt"
dnf5 --assumeyes install git gh gcc gcc-c++ make python3 pkgconf-pkg-config \
    openssl-devel libpq-devel postgresql postgresql-server postgresql-contrib

if [[ ! -f $state_dir/postgres-prepared ]]; then
    postgresql-setup --initdb
    cp -a /var/lib/pgsql/data/pg_hba.conf "$state_dir/pg_hba.conf.original"
    cp -a /var/lib/pgsql/data/postgresql.conf "$state_dir/postgresql.conf.original"
    cat > /var/lib/pgsql/data/pg_hba.conf <<'CONF'
# Desenvolvimento local: identidade Unix no socket e SCRAM no loopback.
local   all   all                         peer
host    all   all   127.0.0.1/32           scram-sha-256
host    all   all   ::1/128                scram-sha-256
CONF
    cat >> /var/lib/pgsql/data/postgresql.conf <<'CONF'

# Ambiente local de desenvolvimento.
listen_addresses = 'localhost'
password_encryption = 'scram-sha-256'
CONF
    chown postgres:postgres /var/lib/pgsql/data/pg_hba.conf
    chmod 0600 /var/lib/pgsql/data/pg_hba.conf
    restorecon /var/lib/pgsql/data/pg_hba.conf /var/lib/pgsql/data/postgresql.conf
    systemctl enable --now postgresql.service
    install -d -m 0700 -o "$developer_account" -g "$developer_group" "$developer_home/.config/local-development"
    python3 - <<'PY'
import os
import pwd
import secrets
import subprocess

account = pwd.getpwnam(os.environ['DEVELOPER_ACCOUNT'])
password = secrets.token_urlsafe(32)
sql = (
    f'CREATE ROLE "{account.pw_name}" LOGIN CREATEDB NOSUPERUSER NOCREATEROLE '
    f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}';\n"
    f'CREATE DATABASE "{account.pw_name}" OWNER "{account.pw_name}";\n'
)
subprocess.run(
    ['runuser', '-u', 'postgres', '--', 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-d', 'postgres'],
    input=sql, text=True, check=True,
)
filename = os.path.join(account.pw_dir, '.config/local-development/postgresql.env')
fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(f'DATABASE_URL=postgresql://{account.pw_name}:{password}@127.0.0.1:5432/{account.pw_name}\n')
os.chown(filename, account.pw_uid, account.pw_gid)
print('Credenciais locais salvas em arquivo privado; senha omitida do registro.')
PY
    restorecon -R "$developer_home/.config/local-development"
    touch "$state_dir/postgres-prepared"
fi

systemctl is-active --quiet postgresql.service
systemctl is-enabled postgresql.service
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d postgres \
    -c "SELECT rolname, rolcreatedb, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = '$developer_account';" \
    -c "SELECT datname FROM pg_database WHERE datname = '$developer_account';" \
    -c 'SHOW listen_addresses;' \
    -c "SELECT line_number, error FROM pg_hba_file_rules WHERE error IS NOT NULL;"
ss -lnt 'sport = :5432'
printf 'SYSTEM_DEVELOPMENT_READY\n'
