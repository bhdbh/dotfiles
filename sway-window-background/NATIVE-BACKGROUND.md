# Fundo nativo das janelas

Esta cópia do Sway 1.11 acrescenta um retângulo ao início da árvore de desenho
de cada janela. Ele fica atrás do conteúdo do aplicativo e participa das mesmas
transações de posição e tamanho, com a mesma ordem de sobreposição e visibilidade.
Não há processo auxiliar, consulta periódica ao IPC nem superfície layer-shell.

## Configuração

```sway
client.background border
```

`border` acompanha a cor `child_border` do estado atual da janela. Na configuração
do tema essa cor é zinc-600, `#52525b`. Também aceita uma cor explícita, como
`client.background #52525b`, ou `client.background none` para desativar.
O padrão dessa compilação é `none`; a configuração do usuário habilita `border`.
As opções funcionam também por `swaymsg` e após recarregar a configuração.

O preenchimento cobre o retângulo inteiro do conteúdo, incluindo transparências
intencionais do aplicativo, e funciona em janelas sobrepostas e em tela cheia.
A opacidade configurada pelo Sway também é aplicada ao fundo.

## Instalação local

- Executável: `/home/user/.local/bin/sway-native`.
- Inicialização: a entrada existente de login no tty1 em
  `~/.config/fish/config.fish` passa a executar esse binário.
- Configuração: `~/.config/sway/config`, com `client.background border`.
- Base: tag oficial `1.11`, commit `990b23c0e584114c3694447344d6bbf44b2d748f`.
- Biblioteca: `wlroots0.19` 0.19.3 instalada no sistema.

A mudança de executável entra em vigor ao encerrar e iniciar novamente a sessão
gráfica. Recarregar apenas a configuração não substitui o compositor em execução.
O auxiliar antigo pode continuar na sessão aberta até ela terminar; a configuração
nativa não inicia esse auxiliar nas próximas sessões.

Essa é uma compilação local: atualizações do pacote Sway da distribuição não
recompilam esta alteração. O executável original continua disponível em
`/usr/bin/sway`; para voltar a ele, altere a linha de inicialização no Fish para
`exec /usr/bin/sway`. Os arquivos anteriores também ficam nos backups do Sway.

## Compilação reproduzível neste ambiente

Os cabeçalhos que faltavam foram extraídos de RPMs Fedora em `.build-deps/`.
Na recuperação para `~/Projects/fedora-setup` foram mantidos os cabeçalhos,
arquivos pkg-config e links para as bibliotecas instaladas. Os RPMs ficaram
no backup. Os caminhos pkg-config são relativos à pasta atual.
A compilação também pode ser feita por `../build.sh sway`, a partir desta pasta.
A cópia gerada em `stage/` pelos comandos abaixo usa as bibliotecas normais do
sistema e não tem RPATH apontando para a pasta de compilação.

```sh
PKG_CONFIG_PATH="$PWD/.build-deps/usr/lib64/pkgconfig" meson setup build \
  --prefix=/usr --sysconfdir=/etc --buildtype=debugoptimized \
  -Dswaybar=false -Dswaynag=false -Dtray=disabled -Dman-pages=disabled
ninja -C build -j2 sway/sway
meson install -C build --destdir "$PWD/stage" --tags runtime
```

## Verificação isolada

O teste usa dois monitores headless, uma janela com cantos transparentes e uma
abertura transparente no centro. Um cliente do protocolo de ponteiro virtual
produz arrastes reais. As capturas verificam que o fundo tem a cor da borda no
mesmo quadro, além de foco, sobreposição, tamanho, tela cheia, scratchpad,
opacidade, comandos inválidos, recarga e fechamento.

```sh
wayland-scanner client-header local-tests/wlr-virtual-pointer-unstable-v1.xml \
  build/virtual-pointer-client-protocol.h
wayland-scanner private-code local-tests/wlr-virtual-pointer-unstable-v1.xml \
  build/virtual-pointer-protocol.c
cc -Wall -Wextra -Wno-unused-parameter -Ibuild local-tests/virtual-pointer.c \
  build/virtual-pointer-protocol.c -lwayland-client -o build/virtual-pointer
python3 local-tests/native-background.py
```

Os testes gravam suas capturas em `native-test-artifacts/` e encerram apenas os
processos que criaram. A sessão gráfica em uso não é afetada.
