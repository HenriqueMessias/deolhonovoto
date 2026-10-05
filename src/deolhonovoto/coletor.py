"""CLI de coleta: descobre eleições, baixa municípios e grava snapshots da apuração.

Exemplos (2026: presidente 1T = 6257, 2T = 6258):

    python -m deolhonovoto.coletor eleicoes
    python -m deolhonovoto.coletor coletar --eleicao 6257
    python -m deolhonovoto.coletor monitorar --eleicao 6258 --intervalo 30

Cada rodada grava:
  data/raw/<ciclo>/<eleicao>/c<cargo>/<timestamp>/<abrangencia>.json.gz  (JSON bruto, p/ replay)
  data/processed/<eleicao>/c<cargo>/totais/<timestamp>.parquet
  data/processed/<eleicao>/c<cargo>/candidatos/<timestamp>.parquet
  data/processed/<eleicao>/progresso/<timestamp>.parquet  (seções totalizadas por município)

Guardar tudo com timestamp é o que permite, depois, reconstruir a ordem em que os
municípios foram apurados e fazer backtest do modelo de nowcast.
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
    parse_progresso,
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
        raise SystemExit(f"Nenhum município em {eleicao.url_municipios(cliente.base)} "
                         "(a eleição já foi publicada pelo TSE?)")
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


def _gravar_raw(pasta: Path, nome: str, payload: dict) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    with gzip.open(pasta / f"{nome}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def gravar_rodada(
    data_dir: Path, eleicao: Eleicao, cargo: int, carimbo: str, resultados: list[tuple[str, dict]]
) -> tuple[int, int]:
    """Grava JSON bruto + parquet normalizado. Retorna (nº abrangências, nº linhas candidatos)."""
    if not resultados:
        return 0, 0
    raw_dir = data_dir / "raw" / eleicao.ciclo / str(eleicao.codigo) / f"c{cargo:04d}" / carimbo

    linhas_totais, linhas_cand = [], []
    for abr, payload in resultados:
        _gravar_raw(raw_dir, abr, payload)
        totais, candidatos = parse_resultado(payload, cargo)
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


def gravar_progresso(data_dir: Path, eleicao: Eleicao, carimbo: str,
                     progresso: list[tuple[str, dict]]) -> pd.DataFrame:
    """Grava os "-ab.json" (bruto + parquet). Retorna as linhas de municípios."""
    if not progresso:
        return pd.DataFrame()
    raw_dir = data_dir / "raw" / eleicao.ciclo / str(eleicao.codigo) / "progresso" / carimbo
    linhas = []
    for uf, payload in progresso:
        _gravar_raw(raw_dir, uf, payload)
        for linha in parse_progresso(payload):
            linhas.append({"coleta_utc": carimbo, "uf": uf.upper(), **linha})
    df = pd.DataFrame(linhas)
    pasta = data_dir / "processed" / str(eleicao.codigo) / "progresso"
    pasta.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pasta / f"{carimbo}.parquet", index=False)
    return df[df["tipo_abrangencia"] == "mun"]


async def rodada(
    cliente: ClienteTSE, eleicao: Eleicao, cargo: int, alvo: list[str], data_dir: Path
) -> tuple[int, list[str]]:
    carimbo = _agora()
    resultados, falhas = await cliente.resultados(eleicao, alvo, cargo)
    n_abr, _ = gravar_rodada(data_dir, eleicao, cargo, carimbo, resultados)
    return n_abr, falhas


def _resumo_br(data_dir: Path, eleicao: Eleicao, cargo: int) -> str:
    pasta = data_dir / "processed" / str(eleicao.codigo) / f"c{cargo:04d}"
    for arq in sorted((pasta / "candidatos").glob("*.parquet"), reverse=True):
        df = pd.read_parquet(arq)
        br = df[df["arquivo"] == "br"]
        if br.empty:
            continue
        tot = pd.read_parquet(pasta / "totais" / arq.name)
        pst = tot.loc[tot["arquivo"] == "br", "pct_secoes_totalizadas"].iloc[0]
        top = br.sort_values("votos", ascending=False).head(4)
        return f"BR {pst:.2f}% seções | " + " | ".join(
            f"{r.nome}: {r.pct_votos_validos:.2f}%" for r in top.itertuples())
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
    if not args.todas:
        df = df[df["ciclo"] == args.ciclo]
    with pd.option_context("display.max_colwidth", 70, "display.width", 200):
        print(df.to_string(index=False))
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
        gravar_progresso(data_dir, eleicao, _agora(), await cliente.progresso(eleicao, UFS))
        n_abr, falhas = await rodada(cliente, eleicao, args.cargo, alvo, data_dir)
    print(f"{n_abr}/{len(alvo)} arquivos gravados em {time.monotonic() - t0:.1f}s")
    if falhas:
        print(f"{len(falhas)} falharam (rode de novo para completar): {falhas[:10]}")
    print(_resumo_br(data_dir, eleicao, args.cargo))


async def cmd_monitorar(args) -> None:
    """Loop de coleta durante a apuração.

    A cada `intervalo` s: BR + UFs + "-ab.json" das UFs; dos municípios, baixa só
    os que tiveram mudança de seções totalizadas desde a última rodada (ou que
    falharam antes). A cada `intervalo_completo` s baixa todos (rede de segurança;
    os que não mudaram respondem 304 graças ao ETag).
    """
    eleicao = Eleicao(args.ciclo, args.eleicao)
    data_dir = Path(args.data_dir)
    async with ClienteTSE(args.base, concorrencia=args.concorrencia) as cliente:
        while True:  # o 2T só é publicado pelo TSE pouco antes da apuração
            try:
                municipios = await baixar_municipios(cliente, eleicao, data_dir)
                break
            except SystemExit as exc:
                print(f"[{_agora()}] {exc}; tentando de novo em {args.intervalo:.0f}s", flush=True)
                await asyncio.sleep(args.intervalo)
        todos_mu = abrangencias({"mu"}, municipios)
        visto: dict[str, tuple] = {}
        pendentes: set[str] = set(todos_mu)
        ultimo_completo = time.monotonic()
        while True:
            t0 = time.monotonic()
            carimbo = _agora()
            try:
                prog = gravar_progresso(data_dir, eleicao, carimbo, await cliente.progresso(eleicao, UFS))
                for r in prog.itertuples():
                    arq = r.uf.lower() + str(r.abrangencia)
                    estado = (r.secoes_totalizadas, r.hora_totalizacao)
                    if visto.get(arq) != estado:
                        visto[arq] = estado
                        pendentes.add(arq)
                if t0 - ultimo_completo >= args.intervalo_completo:
                    pendentes.update(todos_mu)
                    ultimo_completo = t0
                alvo = abrangencias({"br", "uf"}, None) + sorted(pendentes)
                n_abr, falhas = await rodada(cliente, eleicao, args.cargo, alvo, data_dir)
                pendentes = set(falhas) & set(todos_mu)
                print(f"[{carimbo}] {n_abr} novos / {len(alvo)} consultados, {len(falhas)} falhas | "
                      f"{_resumo_br(data_dir, eleicao, args.cargo)}", flush=True)
            except Exception as exc:  # nunca derrubar o loop no meio da apuração
                print(f"[{carimbo}] erro na rodada: {exc!r}", flush=True)
            await asyncio.sleep(max(0.0, args.intervalo - (time.monotonic() - t0)))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="deolhonovoto.coletor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=BASE_URL)
    parser.add_argument("--data-dir", default="data")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("eleicoes", help="lista eleições/turnos disponíveis (ele-c.json)")
    p.add_argument("--ciclo", default="ele2026")
    p.add_argument("--todas", action="store_true", help="inclui ciclos anteriores e suplementares")

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
    p.add_argument("--intervalo", type=float, default=30)
    p.add_argument("--intervalo-completo", type=float, default=900)

    args = parser.parse_args(argv)
    comando = {"eleicoes": cmd_eleicoes, "municipios": cmd_municipios,
               "coletar": cmd_coletar, "monitorar": cmd_monitorar}[args.cmd]
    asyncio.run(comando(args))


if __name__ == "__main__":
    main()
