"""Replays de apuração para backtest do nowcast.

Duas formas de reproduzir uma noite de apuração:

* `estado_real`: usa o horário da última totalização de cada zona eleitoral
  (dados abertos, `detalhe_votacao_munzona`). Cada zona entra inteira no
  instante em que terminou: é a ordem *real* da noite, com granularidade de zona.
* `estado_parcial`: ordem sintética com viés regional (Sul/Sudeste primeiro,
  Norte/Nordeste e exterior depois), útil para testes sem dados reais.

Com os snapshots coletados ao vivo em 2026, dá para usar a sequência exata
(ver `dados.estado_municipal(..., ate=...)`).
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


def estado_real(zonas: pd.DataFrame, instante: pd.Timestamp) -> pd.DataFrame:
    """Estado por município no `instante`, somando as zonas já totalizadas.

    `pct_secoes_totalizadas` é aproximado pela fração do eleitorado do município
    nas zonas já totalizadas.
    """
    chave = ["uf", "cd_municipio_tse"]
    cols = [c for c in zonas.columns if c.startswith("v_")] + [
        "comparecimento", "votos_validos", "votos_brancos", "votos_nulos"]
    pronta = (zonas["totalizado_em"] <= instante).to_numpy()
    parcial = zonas[cols].mul(pronta, axis=0)
    parcial[chave] = zonas[chave]
    parcial["eleit_pronto"] = zonas["eleitorado"] * pronta
    parcial["eleitorado"] = zonas["eleitorado"]
    df = parcial.groupby(chave, as_index=False).sum()
    df["pct_secoes_totalizadas"] = 100 * df["eleit_pronto"] / df["eleitorado"].clip(lower=1)
    return df.drop(columns=["eleit_pronto"])


def _avaliar(t1: pd.DataFrame, final: pd.DataFrame, a: str, b: str, estados, seed: int = 0) -> pd.DataFrame:
    """Roda o nowcast em cada (rótulo, estado parcial) e compara com o resultado final."""
    from .nowcast import projetar

    final_a = 100 * final[f"v_{a}"].sum() / (final[f"v_{a}"].sum() + final[f"v_{b}"].sum())
    linhas = []
    for rotulo, parcial in estados:
        p = projetar(t1, parcial, a, b, seed=seed)
        linhas.append({
            "momento": rotulo, "pct_apurado": p.pct_votos_apurados, "parcial_a": p.pct_a_parcial,
            "projecao_a": p.pct_a, "ic90_inf": p.ic90[0], "ic90_sup": p.ic90[1],
            "prob_a": p.prob_a_vence, "final_a": final_a,
            "erro_parcial": p.pct_a_parcial - final_a, "erro_projecao": p.pct_a - final_a,
        })
    return pd.DataFrame(linhas)


def backtest(t1: pd.DataFrame, t2: pd.DataFrame, a: str, b: str, seed: int = 0,
             taus=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)) -> pd.DataFrame:
    """Backtest com ordem de apuração sintética."""
    chave = ["uf", "cd_municipio_tse"]
    t2 = t2.merge(t1[chave], on=chave)
    inicio = inicio_apuracao(t2, seed)
    estados = ((tau, estado_parcial(t2, inicio, tau)) for tau in taus)
    return _avaliar(t1, t2, a, b, estados, seed)


def backtest_real(t1: pd.DataFrame, zonas_t2: pd.DataFrame, a: str, b: str,
                  passo: str = "15min", seed: int = 0) -> pd.DataFrame:
    """Backtest com a ordem real de totalização das zonas no 2º turno."""
    inicio = zonas_t2["totalizado_em"].min().ceil(passo)
    fim = zonas_t2["totalizado_em"].quantile(0.99)
    instantes = pd.date_range(inicio, fim, freq=passo)
    estados = ((t.strftime("%d/%m %H:%M"), estado_real(zonas_t2, t)) for t in instantes)
    return _avaliar(t1, estado_real(zonas_t2, zonas_t2["totalizado_em"].max()), a, b, estados, seed)
