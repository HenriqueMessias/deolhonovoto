"""Download e leitura dos arquivos consolidados do Portal de Dados Abertos do TSE.

Os resultados oficiais por município/zona ("votacao_candidato_munzona" e
"detalhe_votacao_munzona") são publicados alguns dias após cada turno em
https://dadosabertos.tse.jus.br (arquivos hospedados em cdn.tse.jus.br). Eles
servem para:
  * treinar/validar o modelo com eleições passadas (ex.: 1T -> 2T de 2022);
  * conferir os dados coletados ao vivo depois que o TSE consolidar 2026.

Uso:
    python -m deolhonovoto.dadosabertos 2022            # baixa os zips
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import httpx
import pandas as pd

CDN = "https://cdn.tse.jus.br/estatistica/sead/odsele"
CONJUNTOS = ("votacao_candidato_munzona", "detalhe_votacao_munzona")


def url_zip(conjunto: str, ano: int) -> str:
    return f"{CDN}/{conjunto}/{conjunto}_{ano}.zip"


def baixar(ano: int, data_dir: str | Path = "data") -> list[Path]:
    destino = Path(data_dir) / "dadosabertos"
    destino.mkdir(parents=True, exist_ok=True)
    saida = []
    for conjunto in CONJUNTOS:
        arq = destino / f"{conjunto}_{ano}.zip"
        if not arq.exists():
            print(f"baixando {url_zip(conjunto, ano)}")
            with httpx.stream("GET", url_zip(conjunto, ano), timeout=600, follow_redirects=True) as r:
                r.raise_for_status()
                with open(arq.with_suffix(".part"), "wb") as fh:
                    for bloco in r.iter_bytes(1 << 20):
                        fh.write(bloco)
            arq.with_suffix(".part").rename(arq)
        saida.append(arq)
    return saida


def _ler_csv_do_zip(zip_path: Path, sufixo: str = "_BR.csv") -> pd.DataFrame:
    """Lê o CSV do zip. Os arquivos *_BR.csv trazem os cargos nacionais (presidente)."""
    with zipfile.ZipFile(zip_path) as zf:
        nomes = [n for n in zf.namelist() if n.endswith(sufixo)]
        if not nomes:
            raise FileNotFoundError(f"nenhum *{sufixo} em {zip_path}: {zf.namelist()[:5]}...")
        with zf.open(nomes[0]) as fh:
            return pd.read_csv(fh, sep=";", encoding="latin-1", dtype=str, low_memory=False)


def _num(df: pd.DataFrame, *candidatas: str) -> pd.Series:
    for col in candidatas:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(0)
    return pd.Series(0.0, index=df.index)


CHAVE_ZONA = ["uf", "cd_municipio_tse", "zona"]
TOTAIS = ["eleitorado", "comparecimento", "votos_brancos", "votos_nulos"]


def carregar_zonas(ano: int, turno: int, cargo: int = 1, data_dir: str | Path = "data") -> pd.DataFrame:
    """Resultado final por zona eleitoral (formato largo) + horário da última totalização.

    O horário (`totalizado_em`, horário de Brasília) é o que permite reproduzir a
    ordem real em que a apuração andou na noite da eleição. O resultado fica em
    cache em parquet, porque ler o zip de ~640 MB demora.
    """
    pasta = Path(data_dir) / "dadosabertos"
    cache = pasta / f"zonas_{ano}_t{turno}_c{cargo:04d}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    cand = _ler_csv_do_zip(pasta / f"votacao_candidato_munzona_{ano}.zip")
    det = _ler_csv_do_zip(pasta / f"detalhe_votacao_munzona_{ano}.zip")

    def filtrar(df):
        df = df[(df["NR_TURNO"].astype(int) == turno) & (df["CD_CARGO"].astype(int) == cargo)].copy()
        # um arquivo traz "1120", o outro "01120": normaliza p/ o código TSE de 5 dígitos
        df["uf"] = df["SG_UF"]
        df["cd_municipio_tse"] = df["CD_MUNICIPIO"].str.zfill(5)
        df["zona"] = df["NR_ZONA"].str.zfill(4)
        return df

    cand, det = filtrar(cand), filtrar(det)
    cand["votos"] = _num(cand, "QT_VOTOS_NOMINAIS_VALIDOS", "QT_VOTOS_NOMINAIS")
    largo = cand.pivot_table(index=CHAVE_ZONA, columns="NR_CANDIDATO", values="votos",
                             aggfunc="sum", fill_value=0)
    largo.columns = [f"v_{c}" for c in largo.columns]

    det = det.assign(
        eleitorado=_num(det, "QT_APTOS"),
        comparecimento=_num(det, "QT_COMPARECIMENTO"),
        votos_brancos=_num(det, "QT_VOTOS_BRANCOS"),
        votos_nulos=_num(det, "QT_TOTAL_VOTOS_NULOS", "QT_VOTOS_NULOS"),
        totalizado_em=pd.to_datetime(
            det["DT_ULTIMA_TOTALIZACAO"] + " " + det["HH_ULTIMA_TOTALIZACAO"], dayfirst=True, errors="coerce"),
    ).groupby(CHAVE_ZONA).agg({**{c: "sum" for c in TOTAIS}, "totalizado_em": "max"})

    df = det.join(largo, how="inner").reset_index()
    df["votos_validos"] = df.filter(like="v_").sum(axis=1)
    df.to_parquet(cache, index=False)
    return df


def carregar_munzona(ano: int, turno: int, cargo: int = 1, data_dir: str | Path = "data") -> pd.DataFrame:
    """Resultado final por município no formato largo (ver dados.py)."""
    zonas = carregar_zonas(ano, turno, cargo, data_dir)
    soma = TOTAIS + ["votos_validos"] + [c for c in zonas.columns if c.startswith("v_")]
    df = zonas.groupby(["uf", "cd_municipio_tse"], as_index=False)[soma].sum()
    df["pct_secoes_totalizadas"] = 100.0
    return df


if __name__ == "__main__":
    for ano in sys.argv[1:] or ["2022"]:
        for p in baixar(int(ano)):
            print(p)
