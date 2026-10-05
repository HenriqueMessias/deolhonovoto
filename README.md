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

Endpoints (validados em 05/10/2026; veja `src/deolhonovoto/tse.py`):

```
/oficial/comum/config/ele-c.json                                  -> eleições, turnos e código do 2T (cdt2)
/oficial/ele2026/6257/config/mun-e006257-cm.json                  -> municípios (TSE, IBGE, capital, zonas)
/oficial/ele2026/6257/dados/sp/sp-e006257-ab.json                 -> progresso de cada município da UF
/oficial/ele2026/6257/dados/br/br-c0001-e006257-u.json            -> Brasil
/oficial/ele2026/6257/dados/sp/sp-c0001-e006257-u.json            -> UF
/oficial/ele2026/6257/dados/sp/sp71072-c0001-e006257-u.json       -> município (UF + código TSE)
```

Códigos de 2026 (de `coletor eleicoes`):

| Eleição | 1º turno | 2º turno |
|---|---|---|
| Federal (Presidente) | 6257 | 6258 |
| Estadual (Governador, Senador, Deputados) | 6259 | 6260 |

`c0001` é o cargo (1 = Presidente, 3 = Governador, 5 = Senador). O 2º turno
(6258) ainda devolve 404: o TSE publica a configuração perto da data; o
`monitorar` espera e tenta de novo sozinho. Os arquivos têm ETag, então
rodadas seguidas só baixam o que mudou.

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Passo a passo

### 1. Agora: guardar o 1º turno por município

```bash
python -m deolhonovoto.coletor eleicoes            # lista códigos de eleição/turno
python -m deolhonovoto.coletor coletar --eleicao 6257
```

São 5.786 arquivos (BR + 27 UFs + exterior + 5.757 municípios, sendo 186 cidades
no exterior), ~1 minuto. A soma dos municípios bate exatamente com o total
Brasil (eleitorado, comparecimento, válidos, brancos, nulos e votos por
candidato). Tudo vai para `data/` (bruto em `.json.gz` + parquet normalizado).

### 2. Validar com 2022 (Lula 13 × Bolsonaro 22)

```bash
python -m deolhonovoto.dadosabertos 2022
python -m deolhonovoto.projetar backtest --ano 2022 --a 13 --b 22
```

O backtest usa o horário real de totalização de cada zona eleitoral
(`detalhe_votacao_munzona`) para reproduzir a ordem da noite do 2T de 2022.
Resultado (Lula terminou com 50,90%):

| Horário | % apurado | Placar parcial | Projeção | IC 90% |
|---|---|---|---|---|
| 17:45 | 1,3% | 55,14% | 50,55% | 50,12–50,99 |
| 18:15 | 5,0% | 51,81% | 50,73% | 50,52–50,93 |
| 18:45 | 16,3% | 50,04% | 51,03% | 50,87–51,19 |
| 19:30 | 63,0% | 50,09% | 50,99% | 50,88–51,10 |
| 20:15 | 86,8% | 50,64% | 50,94% | 50,91–50,97 |

Ressalva: no replay cada zona entra inteira no horário da sua *última*
totalização, o que é mais grosseiro que a noite real (seção a seção). Os
snapshots coletados ao vivo em 2026 permitem um backtest exato.

### 3. Noite do 2º turno

```bash
# terminal 1: coleta contínua; a cada 30s baixa BR, UFs e o progresso por UF,
# e só os municípios cujo nº de seções totalizadas mudou
python -m deolhonovoto.coletor monitorar --eleicao 6258 --intervalo 30

# terminal 2: projeção atualizada a cada 60s
python -m deolhonovoto.projetar ao-vivo --eleicao-1t 6257 --eleicao-2t 6258 --a 13 --b 22 --loop 60
```

Saída: `apurado ~35% | parcial 13 47.1% | projeção 13 50.8% (IC90 50.1-51.5) | P(13 vence) 98%`.

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
