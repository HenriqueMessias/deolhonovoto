"""Tabelas no formato comum "um município por linha" usado pelo modelo.

Colunas do formato largo:
    uf, cd_municipio_tse, eleitorado, comparecimento, votos_validos,
    votos_brancos, votos_nulos, pct_secoes_totalizadas, v_<numero> ...

Duas origens produzem esse formato:
  * `estado_municipal`: snapshots coletados ao vivo (coletor.py), pegando a
    leitura mais recente de cada município até um instante opcional `ate`
    (útil para "rebobinar" a noite da apuração num backtest);
  * `dadosabertos.carregar_munzona`: arquivos oficiais consolidados do TSE.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUNAS_TOTAIS = [
    "eleitorado", "comparecimento", "votos_validos", "votos_brancos", "votos_nulos",
    "pct_secoes_totalizadas",
]


def _ler_pasta(pasta: Path, ate: str | None) -> pd.DataFrame:
    arquivos = sorted(pasta.glob("*.parquet")) if pasta.exists() else []
    if ate is not None:
        arquivos = [a for a in arquivos if a.stem <= ate]
    if not arquivos:
        return pd.DataFrame()
    return pd.concat((pd.read_parquet(a) for a in arquivos), ignore_index=True)


def estado_municipal(
    data_dir: str | Path, eleicao: int, cargo: int = 1, ate: str | None = None
) -> pd.DataFrame:
    """Último estado conhecido de cada município (formato largo).

    `ate`: carimbo "YYYYmmddTHHMMSSZ" — ignora coletas posteriores.
    """
    proc = Path(data_dir) / "processed" / str(eleicao) / f"c{cargo:04d}"
    totais = _ler_pasta(proc / "totais", ate)
    cand = _ler_pasta(proc / "candidatos", ate)
    if totais.empty:
        return pd.DataFrame()

    # arquivos de município são "sp71072" (UF + código); BR/UF têm 2 letras.
    totais = totais[totais["arquivo"].str.len() > 2]
    totais = totais.sort_values("coleta_utc").groupby("arquivo", as_index=False).last()
    chave = totais[["arquivo", "coleta_utc"]]

    cand = cand.merge(chave, on=["arquivo", "coleta_utc"], how="inner")
    largo = cand.pivot_table(index="arquivo", columns="numero", values="votos", aggfunc="sum")
    largo.columns = [f"v_{c}" for c in largo.columns]

    df = totais.set_index("arquivo")[COLUNAS_TOTAIS + ["coleta_utc"]].join(largo).reset_index()
    df["uf"] = df["arquivo"].str[:2].str.upper()
    df["cd_municipio_tse"] = df["arquivo"].str[2:].str.zfill(5)
    return df.drop(columns=["arquivo"])


def serie_nacional(data_dir: str | Path, eleicao: int, cargo: int = 1) -> pd.DataFrame:
    """Evolução do total Brasil ao longo da apuração (uma linha por coleta x candidato)."""
    proc = Path(data_dir) / "processed" / str(eleicao) / f"c{cargo:04d}"
    cand = _ler_pasta(proc / "candidatos", None)
    tot = _ler_pasta(proc / "totais", None)
    if cand.empty:
        return cand
    cand = cand[cand["arquivo"] == "br"]
    tot = tot[tot["arquivo"] == "br"][["coleta_utc", "pct_secoes_totalizadas", "hora_totalizacao"]]
    return cand.merge(tot, on="coleta_utc", how="left")
