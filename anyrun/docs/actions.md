# Ações do sistema no AnyRun

O menu `:` oferece `Suspend`, `Log out`, `Reboot` e `Shutdown`.
Todas as ações abrem duas opções: `Confirm` executa a ação indicada na
descrição e `Back` retorna ao menu sem executar comandos.

`Suspend` chama `/home/user/.local/bin/sway-session-idle --suspend`,
reutilizando o bloqueio da sessão antes de suspender.

O histórico registra a ação original assim que o comando confirmado retorna
com sucesso. O plugin usa um arquivo temporário anônimo para capturar erros,
sem esperar que todos os descendentes fechem `stderr`. Isso evita que o
descritor duplicado pelo bloqueador TTY adie o registro até o desbloqueio.
Cancelar ou receber um erro continua sem criar entradas no histórico.

A interface exibe as ações recebidas no histórico sem filtrar seus títulos
por uma lista fixa. Isso inclui `Suspend` e ações personalizadas. Atualizar
somente o plugin não atualiza esse comportamento da interface: o binário
`anyrun` também precisa ser recompilado e o daemon reiniciado.

Teste de regressão com configuração e estado temporários, usando somente
processos Python fictícios:

```sh
python3 -B anyrun-prefix-provider/tests/verify_actions_history.py \
  --provider ~/.local/libexec/anyrun-prefix-provider \
  --actions-plugin .build/cargo/release/libactions.so
```

O teste `actions_history_ui_flow` verifica as linhas realmente visíveis na
janela e a confirmação ao repetir `Suspend`. Compile o executável de testes
com `cargo test --offline --locked --release --manifest-path anyrun/Cargo.toml
--target-dir .build/cargo --package anyrun --no-run` e passe o caminho informado
para `anyrun-prefix-provider/tests/verify_actions_history_ui.py --test-binary`, junto das opções
`--provider` e `--actions-plugin`. O script usa um compositor privado e ações
fictícias; `--screenshot` grava a janela. A opção alternativa `--launcher`
permite comparar um binário instalado com o mesmo histórico isolado.

Alterações: `source/plugins/actions/src/lib.rs` e `actions.ron`.

Compilação:

```sh
cargo build --offline --locked --release --manifest-path source/Cargo.toml --package actions --target-dir /tmp/anyrun-actions-target
```

A verificação usa o provider real e uma cópia isolada da configuração, com
os quatro comandos substituídos por gravações em um arquivo temporário.
Nenhum comando de suspensão, bloqueio, reinício ou encerramento é executado.

`verification.json` registra os cinco grupos de verificações. `install.py`
preserva os arquivos anteriores, instala o plugin e a configuração de forma
atômica e reinicia apenas o daemon do AnyRun. `installed.json` registra o
backup e os hashes dos arquivos instalados.
