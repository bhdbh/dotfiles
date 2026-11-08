# Continuidade do backdrop do AnyRun

Diagnóstico em 4 de novembro de 2026, horário de São Paulo.

O usuário reproduziu uma sequência de escurecer, clarear e escurecer ao
abrir com Super+D. O log preservado em
`../anyrun-opening-diagnosis/reproduction-20261104T143152Z.log` mostra a
destruição do backdrop no HDMI-A-1 às 11:31:37.692484. O primeiro buffer da
janela principal foi enviado às 11:31:37.740716. O relógio monotônico do
coletor mede um intervalo de 48,341 ms. `reproduced-gap.json` contém os eventos.

## Correção

`Screens::sync` conserva o backdrop de cada monitor, inclusive o escolhido
pelo mouse, até o fechamento. A janela principal usa fundo transparente e
desenha a caixa sobre o backdrop existente. Isso elimina tanto o intervalo
sem fundo quanto a aplicação dupla da opacidade. Ao fechar, a caixa é
ocultada antes dos backdrops. A seleção do monitor e o clique fora continuam
funcionando nas duas telas.

O worker remove o WAYLAND_DEBUG do ambiente do daemon antes de restaurar o
ambiente explícito do chamador, mantendo a instrumentação da UI local ao
launcher, como já ocorre com os ajustes GSK do renderer.

Arquivos de produção: `source/anyrun/src/screens.rs`, `app.rs` e `provider.rs`.
O código de inicialização dos aplicativos independentes do daemon foi
preservado nesta cópia da fonte atual.

## Validação

- 20 testes sem interface passaram.
- Seis execuções de testes gráficos passaram em uma sessão virtual privada:
  primeiro frame; permanência do backdrop durante a resolução do mouse;
  quatro combinações de mouse, monitor de teclado e clique fora.
- 24 aberturas consecutivas alternando monitores de 144 Hz e 60 Hz, com as
  resoluções reais de 1920×1080 e 1366×768. Os 3.487 frames analisados não
  mostraram clareamento intermediário nem escurecimento em dobro.
- O protocolo do executável anterior removeu o backdrop antes do buffer
  principal em 24/24 aberturas, com intervalo máximo de 17,051 ms no teste.
  A versão corrigida preservou o backdrop em 24/24, com intervalo descoberto
  igual a zero. `handoff-verification.json` registra a comparação.

O backend virtual não reproduziu a piscada visual nas 24 aberturas do
executável anterior, embora o intervalo entre as superfícies apareça no
protocolo. A reprodução visual relatada pelo usuário e seu log são da sessão
real; os vídeos neste diretório contêm apenas o ambiente virtual de teste.

`verification.json` registra o hash do executável e os artefatos.
`install.py` verifica as fontes e o binário, preserva backups, confere o
cgroup e os pipes e preserva leitores antigos quando necessário. Ele remove
somente o override temporário de coleta e confirma que as janelas existentes
permanecem abertas após reiniciar apenas o AnyRun.

```sh
cargo build --offline --locked --release --manifest-path source/Cargo.toml \
  --package anyrun --target-dir /tmp/anyrun-actions-target
cargo test --offline --locked --release --manifest-path source/Cargo.toml \
  --package anyrun --target-dir /tmp/anyrun-actions-target
python3 record-continuity.py /tmp/anyrun-actions-target/release/anyrun
python3 verify-handoff.py
```
