"""Replays de apuração para backtest do nowcast.

Os arquivos de dados abertos só trazem o resultado final; não dizem em que
ordem as urnas foram apuradas. Para testar o modelo com eleições passadas,
simulamos uma apuração com viés regional parecido com o observado no Brasil
(Sul/Sudeste/Centro-Oeste e capitais tendem a andar mais rápido; Norte/Nordeste
e o exterior chegam mais tarde). Com os snapshots reais coletados em 2026, o
backtest pode usar a ordem verdadeira (ver `dados.estado_municipal(..., ate=...)`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .nowcast import REGIAO

ATRASO_REGIAO = {"S": 0.0, "SE": 0.05, "CO": 0.05, "N": 0.25, "NE": 0.25, "EX": 0.35}


def inicio_apuracao(final: pd.DataFrame, seed: int = 0, vies: float = 1.0) -> np.ndarray:
    """Instante relativo (0-1) em que cada município começa a apurar."""
    rng = np.random.default_rng(seed)
    atraso = final["uf"].map(REGIAO).map(ATRASO_REGIAO).fillna(0.2).to_numpy()
    porte = np.log(final["eleitorado"].clip(lower=1)).to_numpy()
    porte = (porte - porte.min()) / (porte.max() - porte.min() + 1e-9)
    # municípios grandes começam cedo mas demoram a terminar
    return np.clip(vies * atraso + 0.1 * (1 - porte) + rng.uniform(0, 0.35, len(final)), 0, 0.85)


def estado_parcial(final: pd.DataFrame, inicio: np.ndarray, tau: float, duracao: float = 0.25) -> pd.DataFrame:
    """Estado da apuração no instante `tau` (0 = início, ~1.1 = fim)."""
    porte = final["eleitorado"].clip(lower=1).to_numpy()
    dur = duracao * (0.5 + porte / porte.max())  # municípios grandes demoram mais
    f = np.clip((tau - inicio) / dur, 0, 1)
    parcial = final.copy()
    cols = [c for c in final.columns if c.startswith("v_")] + [
        "comparecimento", "votos_validos", "votos_brancos", "votos_nulos"]
    for c in cols:
        parcial[c] = np.round(final[c].to_numpy() * f)
    parcial["pct_secoes_totalizadas"] = 100 * f
    return parcial


def backtest(t1: pd.DataFrame, t2: pd.DataFrame, a: str, b: str, seed: int = 0,
             taus=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)) -> pd.DataFrame:
    from .nowcast import projetar

    chave = ["uf", "cd_municipio_tse"]
    t2 = t2.merge(t1[chave], on=chave)
    final_a = 100 * t2[f"v_{a}"].sum() / (t2[f"v_{a}"].sum() + t2[f"v_{b}"].sum())
    inicio = inicio_apuracao(t2, seed)
    linhas = []
    for tau in taus:
        p = projetar(t1, estado_parcial(t2, inicio, tau), a, b, seed=seed)
        linhas.append({
            "tau": tau, "pct_apurado": p.pct_votos_apurados, "parcial_a": p.pct_a_parcial,
            "projecao_a": p.pct_a, "ic90_inf": p.ic90[0], "ic90_sup": p.ic90[1],
            "prob_a": p.prob_a_vence, "final_a": final_a,
            "erro_parcial": p.pct_a_parcial - final_a, "erro_projecao": p.pct_a - final_a,
        })
    return pd.DataFrame(linhas)
