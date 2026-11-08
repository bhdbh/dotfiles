# Calculadora do Anyrun

O plugin Rink exibe resultados com até seis casas decimais, arredondados,
sem zeros desnecessários, colchetes de dízimas, frações alternativas ou
rótulos de grandezas. Valores muito pequenos ou grandes usam `e`.

Exemplos de entrada (após o prefixo `=`):

| Entrada | Resultado |
| --- | --- |
| `16/3` | `5.333333` |
| `2/3` | `0.666667` |
| `2+2` | `4` |
| `200 * 15%` | `30` |
| `sqrt(2)` | `1.414214` |
| `2^10` | `1024` |
| `1 meter to cm` | `100 centimeter` |

Os resultados continuam editáveis no mesmo campo. Continuar uma conta usa
o valor arredondado exibido. Formatos pedidos explicitamente, como
`16/3 to fraction`, `255 to hex` e `1/11 to digits 10`, são preservados.

O arredondamento decimal usa os números racionais do Rink diretamente,
sem converter contas exatas para ponto flutuante. As conversões preservam
a escala e a unidade de destino.

Código alterado: `source/plugins/rink/src/lib.rs` e `everyday.rs`.

Validação:

```sh
cargo test --offline --locked --release --manifest-path source/Cargo.toml --package rink --target-dir /tmp/anyrun-pointer/target
cargo build --offline --locked --release --manifest-path source/Cargo.toml --package rink --target-dir /tmp/anyrun-pointer/target
python3 verify.py /tmp/anyrun-pointer/target/release/librink.so
```
