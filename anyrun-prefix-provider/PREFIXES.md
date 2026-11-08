# Anyrun: configuração final

A configuração ativa está em `~/.config/anyrun/config.ron`.

## Uso

| Entrada | Resultado |
| --- | --- |
| Sem prefixo | Busca normal de aplicativos e arquivos |
| `>` / `> nome` | Aplicativos mais usados / busca de aplicativos |
| `#` / `# nome` | Pastas mais usadas / busca de pastas |
| `@` / `@ nome` | Arquivos mais usados / busca de arquivos |
| `:` / `: nome` | Suspend, Log out, Reboot e Shutdown / filtro dessas ações |
| `$` / `$ comando` | Comandos mais usados / execução de comando |
| `=` | Até seis operações recentes |
| `= 2+2` + Enter | Calcula no campo e mantém o launcher aberto |
| `?` | Referência dos sete prefixos |

Espaços depois dos prefixos são opcionais. O limite normal é de seis resultados.
A ajuda exibe os sete prefixos e não recebe seleção ou cliques.

`Suspend` pede confirmação e chama `~/.local/bin/sway-session-idle --suspend`,
que bloqueia a sessão antes de suspender.

Na abertura, os seis itens recentes são preparados antes de mostrar a janela.
O histórico reúne aplicativos, arquivos, diretórios, comandos, ações e cálculos;
abrir um submenu ou cancelar uma confirmação não registra uma execução.
Limpar o campo esvazia a lista. Os contadores de uso conservam seis registros
por prefixo, ordenados por frequência e depois pelo uso mais recente.

O estado de uso fica em `~/.local/state/anyrun/`:

- `history.json`: até seis itens recentes.
- `usage.json`: até seis registros para cada um de `>`, `#`, `@` e `$`.
- `calculations.json`: até seis operações concluídas.

Enter calcula a expressão iniciada por `=`, posiciona o cursor no fim e permite
continuar a conta. Selecionar uma operação recupera seu resultado no campo.
Expressões inválidas mantêm o texto; repetir um resultado inalterado não cria
outra operação.

## Pesquisa e interface

`search_delay_ms: 150` aguarda uma pausa de 150 ms na digitação. A lista anterior
permanece até todos os plugins responderem à consulta atual. Respostas antigas
são descartadas; resultados idênticos reutilizam suas linhas. Enter durante a
espera dispara a consulta e aguarda os resultados novos antes da ativação.
Apagar o campo e abrir a ajuda são imediatos.

O painel tem 480 px, limitado à área disponível, com reticências para textos
longos. Títulos e prefixos usam 16 px; descrições e caminhos, 14 px, alinhados à
esquerda. As bordas usam zinc-600, com 2 px. A divisória da lista aparece somente
quando há resultados.

O monitor preferido é identificado pelo fabricante, modelo e serial do Acer
QG241Y; `eDP-1` é o fallback. O posicionamento e os fundos dos outros monitores
são preparados antes da abertura. Clicar fora do painel fecha o launcher.
O serviço usa `GSK_RENDERER=cairo` e `GSK_DEBUG=full-redraw` para evitar rastros.
Essas variáveis não são repassadas aos aplicativos iniciados pelo provider.

## Arquivos e drives

Arquivos oferecem `Open`, `Copy`, `Copy path` e `Back`; pastas oferecem `Open`,
`Copy path` e `Back`. `Open` delega ao `xdg-open`. Os caminhos são mantidos
absolutos internamente e exibidos com `~/` dentro da home.

| Tipo | Aplicativo padrão |
| --- | --- |
| Pastas | Yazi no Foot |
| Texto, código e configurações | VS Code |
| Imagens | imv |
| Vídeos | mpv |
| Música | ncmpcpp + MPD |
| URLs HTTP/HTTPS | Brave |

As associações ficam em `~/.config/mimeapps.list` e as entradas de aplicativos,
em `~/.local/share/applications/`. O Yazi aplica os mesmos programas por
`~/.config/yazi/yazi.toml`. Seu setup está documentado em
`~/.local/share/yazi/SETUP.md`.

O `drive-manager.service` monta USBs via UDisks e mantém links em
`~/Drives/<label>`. O Kidex acompanha os links e as montagens, indexando os
arquivos acessíveis sem duplicar os caminhos reais dos volumes. Uma
desmontagem manual é respeitada até a próxima conexão. Volumes criptografados
passam a ser acompanhados após o desbloqueio.

O índice acompanha criação, renomeação e exclusão de arquivos. Ocultos são
ignorados; `~/.config/anyrun/search.ignore` exclui dependências, caches, logs e
saídas geradas com sintaxe gitignore. Alterações nesse arquivo são recarregadas
automaticamente. Nas buscas sem `/`, nomes que começam com a consulta e caminhos
mais curtos têm prioridade sobre correspondências nos diretórios pais.

## Arquivos e serviços ativos

| Componente | Caminho |
| --- | --- |
| Interface | `~/.cargo/bin/anyrun` |
| Provider personalizado | `~/.local/libexec/anyrun-prefix-provider` |
| Indexador | `~/.cargo/bin/kidex` |
| Cliente do indexador | `~/.cargo/bin/kidex-client` |
| Plugins | `~/.config/anyrun/plugins/` |
| Configuração do indexador | `~/.config/kidex.ron` |
| Montagem de USBs | `~/.local/libexec/drive-manager` |
| Unidades de usuário | `~/.config/systemd/user/` |

O Sway importa o ambiente gráfico e inicia `anyrun.service` no login da sessão.
`kidex.service` e `drive-manager.service` estão habilitados no usuário.

## Manutenção

Os fontes personalizados e testes estão em `~/.local/src/anyrun`,
`~/.local/src/anyrun-prefix-provider`, `~/.local/src/kidex` e
`~/.local/src/drive-manager`. As cópias dos fontes em `/opt/src/anyrun` e
`/opt/src/kidex` também foram mantidas. A interface e o provider compartilham o
IPC local de `anyrun-prefix-provider/anyrun-provider-ipc`; alterações nesse
protocolo exigem atualizar os dois executáveis juntos.

Para recompilar, os diretórios de build são recriados automaticamente:

```sh
cargo build --offline --locked --release --manifest-path /home/user/.local/src/anyrun/Cargo.toml --package anyrun --package kidex --package actions --target-dir /home/user/.cache/anyrun-prefix-build
cargo build --offline --locked --release --manifest-path /home/user/.local/src/anyrun-prefix-provider/Cargo.toml --target-dir /home/user/.cache/anyrun-prefix-build
cargo build --offline --locked --release --manifest-path /home/user/.local/src/kidex/Cargo.toml --package kidex --target-dir /home/user/.cache/kidex-usb-build
```

Os testes funcionais do provider estão em `tests/verify_prefixes.py`,
`tests/verify_history_limits.py` e `tests/verify_search_ignore.py`.
`tests/verify_search_ui.py` verifica debounce, respostas atrasadas, Enter,
submenus e calculadora em um compositor separado, sem capturas de tela.
Os testes de montagem estão em `~/.local/src/drive-manager/`.

A finalização removeu backups, registros antigos de instalação, prévias,
caches de compilação e arquivos temporários do setup.
