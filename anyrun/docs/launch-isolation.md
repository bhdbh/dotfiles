# Aplicativos encerrados ao reiniciar o AnyRun

Diagnóstico em 4 de novembro de 2026, horário de São Paulo.

As quedas simultâneas do Brave e do VS Code às 10:58:46 e 11:04:48 ocorreram
durante reinícios de `anyrun.service`. Os processos principais estavam em
scopes próprios, mas os zygotes e outros subprocessos ainda pertenciam ao
cgroup do AnyRun. `KillMode=control-group` encerrava esses subprocessos,
derrubando os aplicativos com SIGTRAP. Os dois reinícios foram feitos pelo
agente para aplicar ajustes e ativar o diagnóstico de abertura.

A inspeção da sessão confirmou oito subprocessos do Code no cgroup do
launcher, enquanto o processo principal estava em
`app-com.microsoft.VSCode-136354.scope`. `protect-open-apps.py` moveu somente
esses subprocessos para um scope próprio, gerenciado pelo systemd, sem
enviar sinais ou fechar janelas. `protected-apps.json` registra a migração.
Uma conferência adicional protegeu também o Foot e o Yazi que já estavam
abertos pelo mecanismo antigo. `installed-verification.json` confirma que
restaram apenas o wrapper de diagnóstico e o daemon no cgroup do AnyRun;
o PID do Code foi preservado e o daemon não foi reiniciado nesta correção.

O método `AttachProcessesToUnit` não aceita a anexação direta ao scope do
Code porque ele não é delegado. A migração efetiva usa `StartTransientUnit`
com a lista dos PIDs existentes para criar um scope independente.

## Correção

`anyrun-plugin/src/launch.rs` abre comandos em serviços transitórios do
systemd do usuário, com `ExitType=cgroup`, para manter todos os descendentes
independentes do launcher. Ambiente, diretório e argumentos são preservados;
a saída usa o journal próprio. Metadados de serviço e descritores do AnyRun
não são propagados ao novo serviço.

A função é usada nas três rotas do plugin Applications (programa normal,
terminal configurado e terminal detectado), no Shell e no Open do Kidex.
Os plugins são recarregados pelo provider a cada abertura do launcher. A
instalação substitui somente os três plugins e suas fontes, com backup,
sem reiniciar o daemon ou os aplicativos.

## Validação

`verify-launch.py` usa o provider real, cinco aplicativos simulados e um
serviço de launcher descartável. Cada aplicativo cria um subprocesso e
move somente o processo principal para outro scope, reproduzindo o estado
observado no Chromium/Electron. O teste encerra apenas o launcher de teste.

Com os plugins anteriores, os cinco aplicativos perdem o subprocesso.
Com os plugins corrigidos, os cinco subprocessos sobrevivem. O teste também
valida o ambiente, diretórios e caminhos com espaços, apóstrofos e argumentos
contendo `$`. Nenhuma janela ou perfil real é aberto pelo teste.

Resultados: `baseline.json` e `verification.json`. `installed.json` registra
os hashes instalados e o backup. Os journals foram preservados neste
diretório; não houve evidência de OOM nas duas quedas recentes.

As quedas antigas do Code com SIGSEGV durante a inicialização são tratadas
separadamente em `../vscode-startup-diagnosis/README.md`.

```sh
cargo build --offline --locked --release --manifest-path source/Cargo.toml \
  --package applications --package shell --package kidex --target-dir /tmp/anyrun-actions-target
python3 verify-launch.py /tmp/anyrun-actions-target/release
```
