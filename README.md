# De olho no voto

Coleta da apuração das eleições 2026 direto da fonte usada pelo site do TSE
(https://resultados.tse.jus.br) e **projeção do resultado final do 2º turno em
tempo real** a partir dos resultados do 1º turno.

## De onde vêm os dados

| Fonte | O que tem | Quando | Uso aqui |
|---|---|---|---|
| `resultados.tse.jus.br/oficial/...` (JSONs do app de resultados) | Totais e votos por candidato para BR, UF e **cada município**, com % de seções totalizadas | **Ao vivo** durante a apuração, e fica no ar depois | Coleta do 1T (final) e do 2T (ao vivo) |
| [Dados Abertos do TSE](https://dadosabertos.tse.jus.br) (`cdn.tse.jus.br/estatistica/sead/odsele/...`) | Resultados consolidados por município/zona e por seção, perfil do eleitorado, candidatos | Resultados saem **dias depois** do turno | Treino/backtest com eleições passadas (2022) e conferência |
| Boletins de urna (`.../arquivo-urna/...`) | Arquivo de cada urna (BU, RDV, log) | Ao vivo | Possível evolução: granularidade de seção |

**Resposta curta:** o Portal de Dados Abertos *não* tem a apuração em tempo real —
as tags "Ano 2026" hoje trazem candidatos, eleitorado etc.; os resultados
consolidados entram alguns dias após cada turno. A apuração ao vivo vem dos JSONs
públicos que o próprio app do TSE consome, e é isso que `coletor.py` lê.

Estrutura dos endpoints (veja `src/deolhonovoto/tse.py`):

```
/oficial/comum/config/ele-c.json                                   -> eleições e turnos
/oficial/ele2026/6257/config/mun-e006257-cm.json                   -> municípios
/oficial/ele2026/6257/dados-simplificados/br/br-c0001-e006257-r.json        -> Brasil
/oficial/ele2026/6257/dados-simplificados/sp/sp-c0001-e006257-r.json        -> UF
/oficial/ele2026/6257/dados-simplificados/sp/sp71072-c0001-e006257-r.json   -> município
```

`6257` é o código da eleição que aparece na URL do app (`#/eleicao/6257/...`);
`c0001` é o cargo (1 = Presidente, 3 = Governador, 5 = Senador). Os endpoints
não são documentados oficialmente: antes da noite do 2T, confira no DevTools do
navegador (aba *Network*) se os caminhos continuam iguais e qual é o código da
eleição do 2º turno (também listado por `coletor eleicoes`).

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Passo a passo

### 1. Agora: guardar o 1º turno por município

```bash
python -m deolhonovoto.coletor eleicoes                 # lista códigos de eleição/turno
python -m deolhonovoto.coletor coletar --eleicao 6257 --niveis br,uf,mu
```

São ~5.600 arquivos (5.570 municípios + exterior); com concorrência 16 leva poucos
minutos. Tudo é gravado em `data/` (bruto em `.json.gz` + parquet normalizado).

### 2. Treinar/validar com 2022 (Lula 13 × Bolsonaro 22)

```bash
python -m deolhonovoto.dadosabertos 2022
python -m deolhonovoto.projetar backtest --ano 2022 --a 13 --b 22
```

O backtest simula uma apuração com o viés regional típico (Sul/Sudeste primeiro,
Norte/Nordeste e exterior depois) e compara o placar parcial com a projeção.

### 3. Noite do 2º turno

```bash
# terminal 1: coleta contínua (BR/UFs a cada 60s, municípios a cada 180s)
python -m deolhonovoto.coletor monitorar --eleicao <codigo-2T> --intervalo 60 --intervalo-municipios 180

# terminal 2: projeção atualizada a cada 60s
python -m deolhonovoto.projetar ao-vivo --eleicao-1t 6257 --eleicao-2t <codigo-2T> --a <num> --b <num> --loop 60
```

Saída: `apurado ~35% | parcial A 47.1% | projeção A 50.8% (IC90 50.1-51.5) | P(A vence) 98%`.

## O modelo (`nowcast.py`)

O placar parcial é enviesado porque a ordem de apuração não é aleatória (em 2022
Bolsonaro liderou até ~67% das urnas apuradas e Lula venceu). O modelo:

1. calcula atributos do 1T por município: % de cada finalista, % dos demais
   candidatos, abstenção, brancos/nulos, porte e região;
2. com os municípios já apurados no 2T, ajusta uma ridge ponderada por votos
   (em logit) de "atributos do 1T → % do finalista A no 2T", encolhida para um
   palpite inicial (votos dos demais candidatos divididos meio a meio);
3. prevê o % de A nos votos **ainda não apurados** de cada município (inclusive
   o restante dos parcialmente apurados) e estima o comparecimento do 2T;
4. estima efeitos por UF com encolhimento e simula choques de UF + nacional para
   obter intervalo de 90% e probabilidade de vitória.

Os snapshots com carimbo de hora permitem rebobinar a noite (`--ate`) e refazer
o backtest com a ordem *real* de apuração.

## Próximos passos sugeridos

- Covariáveis extras por município: perfil do eleitorado (dados abertos TSE:
  idade, escolaridade, gênero), IBGE (renda, urbanização), resultado de 2018/2022.
- Prior aprendido em eleições passadas (2018 e 2022, 1T → 2T) no lugar do 50/50.
- Granularidade de zona/seção (boletins de urna) para corrigir também o viés
  *dentro* de municípios grandes como São Paulo e Rio.
- Governadores: mesmo pipeline com `--cargo 3` nas UFs com 2º turno.
