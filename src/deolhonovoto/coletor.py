"""CLI de coleta: descobre eleições, baixa municípios e grava snapshots da apuração.

Exemplos:

    python -m deolhonovoto.coletor eleicoes
    python -m deolhonovoto.coletor municipios --eleicao 6257
    python -m deolhonovoto.coletor coletar --eleicao 6257 --niveis br,uf,mu
    python -m deolhonovoto.coletor monitorar --eleicao <codigo-2T> --intervalo 60

Cada rodada grava:
  data/raw/<ciclo>/<eleicao>/<timestamp>/<abrangencia>.json.gz   (JSON bruto, p/ replay)
  data/processed/<eleicao>/totais/<timestamp>.parquet
  data/processed/<eleicao>/candidatos/<timestamp>.parquet

Guardar o bruto com timestamp é o que permite, depois, reconstruir a ordem em
que os municípios foram apurados e fazer backtest do modelo de nowcast.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .tse import (
    BASE_URL,
    CARGO_PRESIDENTE,
    ClienteTSE,
    Eleicao,
    iter_eleicoes,
    parse_resultado,
)

UFS = [
    "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa",
    "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to", "zz",
]


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def arquivo_municipios(data_dir: Path, eleicao: Eleicao) -> Path:
    return data_dir / "ref" / f"municipios_{eleicao.codigo}.csv"


async def baixar_municipios(cliente: ClienteTSE, eleicao: Eleicao, data_dir: Path) -> pd.DataFrame:
    destino = arquivo_municipios(data_dir, eleicao)
    if destino.exists():
        return pd.read_csv(destino, dtype=str)
    df = pd.DataFrame(await cliente.municipios(eleicao))
    if df.empty:
        raise SystemExit(f"Nenhum município retornado por {eleicao.url_municipios(cliente.base)}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(destino, index=False)
    return df.astype(str)


def abrangencias(niveis: set[str], municipios: pd.DataFrame | None) -> list[str]:
    alvo: list[str] = []
    if "br" in niveis:
        alvo.append("br")
    if "uf" in niveis:
        alvo += UFS
    if "mu" in niveis and municipios is not None:
        alvo += (municipios["uf"].str.lower() + municipios["cd_municipio_tse"]).tolist()
    return alvo


def gravar_rodada(
    data_dir: Path, eleicao: Eleicao, cargo: int, carimbo: str, resultados: list[tuple[str, dict]]
) -> tuple[int, int]:
    """Grava JSON bruto + parquet normalizado. Retorna (nº abrangências, nº linhas candidatos)."""
    if not resultados:
        return 0, 0
    raw_dir = data_dir / "raw" / eleicao.ciclo / str(eleicao.codigo) / f"c{cargo:04d}" / carimbo
    raw_dir.mkdir(parents=True, exist_ok=True)

    linhas_totais, linhas_cand = [], []
    for abr, payload in resultados:
        with gzip.open(raw_dir / f"{abr}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        totais, candidatos = parse_resultado(payload)
        base = {"coleta_utc": carimbo, "arquivo": abr, "cargo": cargo}
        linhas_totais.append({**base, **totais})
        for cand in candidatos:
            linhas_cand.append({**base, "abrangencia": totais["abrangencia"], **cand})

    proc = data_dir / "processed" / str(eleicao.codigo) / f"c{cargo:04d}"
    for nome, linhas in (("totais", linhas_totais), ("candidatos", linhas_cand)):
        if linhas:
            (proc / nome).mkdir(parents=True, exist_ok=True)
            pd.DataFrame(linhas).to_parquet(proc / nome / f"{carimbo}.parquet", index=False)
    return len(linhas_totais), len(linhas_cand)


async def rodada(
    cliente: ClienteTSE, eleicao: Eleicao, cargo: int, alvo: list[str], data_dir: Path
) -> tuple[int, int]:
    carimbo = _agora()
    resultados = await cliente.resultados(eleicao, alvo, cargo)
    return gravar_rodada(data_dir, eleicao, cargo, carimbo, resultados)


def _resumo_br(data_dir: Path, eleicao: Eleicao, cargo: int) -> str:
    pasta = data_dir / "processed" / str(eleicao.codigo) / f"c{cargo:04d}" / "candidatos"
    arquivos = sorted(pasta.glob("*.parquet")) if pasta.exists() else []
    for arq in reversed(arquivos):
        df = pd.read_parquet(arq)
        br = df[df["arquivo"] == "br"]
        if not br.empty:
            top = br.sort_values("votos", ascending=False).head(4)
            return " | ".join(f"{r.nome}: {r.pct_votos_validos:.2f}%" for r in top.itertuples())
    return ""


async def cmd_eleicoes(args) -> None:
    async with ClienteTSE(args.base) as cliente:
        config = await cliente.config()
    if not config:
        raise SystemExit("Não foi possível baixar ele-c.json")
    destino = Path(args.data_dir) / "ref" / "ele-c.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    df = pd.DataFrame(list(iter_eleicoes(config)))
    print(df.drop(columns=["bruto"]).to_string(index=False))
    print(f"\nConfig completa salva em {destino}")


async def cmd_municipios(args) -> None:
    eleicao = Eleicao(args.ciclo, args.eleicao)
    async with ClienteTSE(args.base) as cliente:
        df = await baixar_municipios(cliente, eleicao, Path(args.data_dir))
    print(f"{len(df)} municípios -> {arquivo_municipios(Path(args.data_dir), eleicao)}")


async def cmd_coletar(args) -> None:
    eleicao = Eleicao(args.ciclo, args.eleicao)
    data_dir = Path(args.data_dir)
    niveis = set(args.niveis.split(","))
    async with ClienteTSE(args.base, concorrencia=args.concorrencia) as cliente:
        municipios = await baixar_municipios(cliente, eleicao, data_dir) if "mu" in niveis else None
        alvo = abrangencias(niveis, municipios)
        t0 = time.monotonic()
        n_abr, n_cand = await rodada(cliente, eleicao, args.cargo, alvo, data_dir)
    print(f"{n_abr}/{len(alvo)} arquivos novos, {n_cand} linhas de candidatos em {time.monotonic() - t0:.1f}s")
    print(_resumo_br(data_dir, eleicao, args.cargo))


async def cmd_monitorar(args) -> None:
    """Loop: BR+UFs a cada `intervalo` s; municípios a cada `intervalo_municipios` s."""
    eleicao = Eleicao(args.ciclo, args.eleicao)
    data_dir = Path(args.data_dir)
    async with ClienteTSE(args.base, concorrencia=args.concorrencia) as cliente:
        municipios = await baixar_municipios(cliente, eleicao, data_dir)
        alvo_rapido = abrangencias({"br", "uf"}, None)
        alvo_mu = abrangencias({"mu"}, municipios)
        ultimo_mu = 0.0
        while True:
            t0 = time.monotonic()
            alvo = list(alvo_rapido)
            if t0 - ultimo_mu >= args.intervalo_municipios:
                alvo += alvo_mu
                ultimo_mu = t0
            try:
                n_abr, _ = await rodada(cliente, eleicao, args.cargo, alvo, data_dir)
                print(f"[{_agora()}] {n_abr} arquivos novos ({len(alvo)} consultados) "
                      f"{_resumo_br(data_dir, eleicao, args.cargo)}", flush=True)
            except Exception as exc:  # nunca derrubar o loop no meio da apuração
                print(f"[{_agora()}] erro na rodada: {exc!r}", flush=True)
            await asyncio.sleep(max(0.0, args.intervalo - (time.monotonic() - t0)))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="deolhonovoto.coletor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=BASE_URL)
    parser.add_argument("--data-dir", default="data")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("eleicoes", help="lista eleições/turnos disponíveis (ele-c.json)")

    def comum(p):
        p.add_argument("--ciclo", default="ele2026")
        p.add_argument("--eleicao", type=int, required=True, help="código da eleição (ex.: 6257)")
        p.add_argument("--cargo", type=int, default=CARGO_PRESIDENTE)
        p.add_argument("--concorrencia", type=int, default=16)

    comum(sub.add_parser("municipios", help="baixa a lista de municípios da eleição"))
    p = sub.add_parser("coletar", help="uma rodada de coleta")
    comum(p)
    p.add_argument("--niveis", default="br,uf,mu", help="br,uf,mu")
    p = sub.add_parser("monitorar", help="coleta contínua durante a apuração")
    comum(p)
    p.add_argument("--intervalo", type=float, default=60)
    p.add_argument("--intervalo-municipios", type=float, default=180)

    args = parser.parse_args(argv)
    comando = {"eleicoes": cmd_eleicoes, "municipios": cmd_municipios,
               "coletar": cmd_coletar, "monitorar": cmd_monitorar}[args.cmd]
    asyncio.run(comando(args))


if __name__ == "__main__":
    main()
