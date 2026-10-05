"""CLI de projeção.

Ao vivo (usa os snapshots coletados por coletor.py):
    python -m deolhonovoto.projetar ao-vivo --eleicao-1t 6257 --eleicao-2t <cod> --a 13 --b 22

Backtest com dados abertos (ex.: 2022, Lula=13 x Bolsonaro=22), reproduzindo a
ordem real em que as zonas eleitorais foram totalizadas na noite do 2T:
    python -m deolhonovoto.dadosabertos 2022
    python -m deolhonovoto.projetar backtest --ano 2022 --a 13 --b 22
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from .dadosabertos import carregar_munzona, carregar_zonas
from .dados import estado_municipal
from .nowcast import projetar
from .simulacao import backtest, backtest_real


def cmd_ao_vivo(args) -> None:
    t1 = estado_municipal(args.data_dir, args.eleicao_1t, args.cargo)
    if t1.empty:
        raise SystemExit("Sem dados do 1T: rode o coletor com --eleicao do 1º turno e --niveis mu")
    while True:
        t2 = estado_municipal(args.data_dir, args.eleicao_2t, args.cargo, ate=args.ate)
        if t2.empty:
            print("aguardando dados do 2T...")
        else:
            p = projetar(t1, t2, args.a, args.b)
            print(time.strftime("%H:%M:%S"), p.resumo(args.a, args.b), flush=True)
        if not args.loop:
            break
        time.sleep(args.loop)


def cmd_backtest(args) -> None:
    t1 = carregar_munzona(args.ano, 1, args.cargo, args.data_dir)
    pd.set_option("display.width", 200)
    if args.ordem == "real":
        zonas = carregar_zonas(args.ano, 2, args.cargo, args.data_dir)
        print("# replay com a ordem real de totalização das zonas (horário de Brasília)")
        print(backtest_real(t1, zonas, args.a, args.b, passo=args.passo).round(3).to_string(index=False))
        return
    t2 = carregar_munzona(args.ano, 2, args.cargo, args.data_dir)
    for seed in range(args.repeticoes):
        print(f"\n# replay seed={seed}")
        print(backtest(t1, t2, args.a, args.b, seed=seed).round(3).to_string(index=False))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="deolhonovoto.projetar", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--cargo", type=int, default=1)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ao-vivo")
    p.add_argument("--eleicao-1t", type=int, required=True)
    p.add_argument("--eleicao-2t", type=int, required=True)
    p.add_argument("--a", required=True, help="número do finalista A")
    p.add_argument("--b", required=True, help="número do finalista B")
    p.add_argument("--ate", help="carimbo YYYYmmddTHHMMSSZ p/ rebobinar")
    p.add_argument("--loop", type=float, default=0, help="repetir a cada N segundos")
    p.set_defaults(func=cmd_ao_vivo)

    p = sub.add_parser("backtest")
    p.add_argument("--ano", type=int, default=2022)
    p.add_argument("--a", default="13")
    p.add_argument("--b", default="22")
    p.add_argument("--ordem", choices=["real", "simulada"], default="real",
                   help="real = horário de totalização de cada zona; simulada = viés regional sintético")
    p.add_argument("--passo", default="15min", help="intervalo entre projeções no replay real")
    p.add_argument("--repeticoes", type=int, default=3, help="nº de replays (ordem simulada)")
    p.set_defaults(func=cmd_backtest)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
