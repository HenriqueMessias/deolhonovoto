"""Projeção do resultado final do 2º turno a partir da apuração parcial.

Ideia (a mesma das "agulhas" de noite eleitoral):

1. Para cada município calculamos atributos do 1º turno: fatia de votos de cada
   finalista, fatia dos "outros" candidatos, abstenção, brancos+nulos, porte e região.
2. Durante a apuração do 2T, os municípios já (parcialmente) apurados mostram
   como o eleitorado de fato migrou do 1T para o 2T. Ajustamos uma regressão
   (ridge ponderada pelos votos) "atributos do 1T -> % do finalista A no 2T"
   apenas com esses municípios, partindo de um palpite a priori (os votos dos
   outros candidatos se dividem meio a meio) para não sobreajustar no início.
3. Prevemos o % de A nos votos ainda não apurados de cada município e somamos.
4. Os erros não são independentes: a ordem de apuração não é aleatória
   (fusos, capitais, regiões). Estimamos um efeito por UF com encolhimento e
   simulamos choques de UF + nacional para obter intervalo e P(vitória).

A apuração parcial bruta é enviesada (ex.: em 2022 Bolsonaro liderou até ~67%
das urnas apuradas); o objetivo do modelo é corrigir exatamente esse viés.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REGIAO = {
    "AC": "N", "AM": "N", "AP": "N", "PA": "N", "RO": "N", "RR": "N", "TO": "N",
    "AL": "NE", "BA": "NE", "CE": "NE", "MA": "NE", "PB": "NE", "PE": "NE", "PI": "NE", "RN": "NE", "SE": "NE",
    "DF": "CO", "GO": "CO", "MS": "CO", "MT": "CO",
    "ES": "SE", "MG": "SE", "RJ": "SE", "SP": "SE",
    "PR": "S", "RS": "S", "SC": "S",
    "ZZ": "EX",
}
REGIOES = ["N", "NE", "CO", "SE", "S", "EX"]


def atributos_1t(t1: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    """Atributos do 1º turno por município. `a`/`b` = números dos finalistas."""
    vv = t1["votos_validos"].clip(lower=1)
    comp = t1["comparecimento"].clip(lower=1)
    x = pd.DataFrame(index=t1.index)
    x["s_a"] = t1[f"v_{a}"] / vv
    x["s_b"] = t1[f"v_{b}"] / vv
    x["s_outros"] = (1 - x["s_a"] - x["s_b"]).clip(lower=0)
    x["abst"] = 1 - comp / t1["eleitorado"].clip(lower=1)
    x["bn"] = (t1["votos_brancos"] + t1["votos_nulos"]) / comp
    x["log_eleit"] = np.log(t1["eleitorado"].clip(lower=1))
    for r in REGIOES:
        x[f"r_{r}"] = (t1["uf"].map(REGIAO) == r).astype(float)
    return x


def palpite_inicial(x: pd.DataFrame) -> pd.Series:
    """Prior: A mantém sua fatia relativa vs B e herda metade dos votos dos outros."""
    return (x["s_a"] + 0.5 * x["s_outros"]).clip(0.001, 0.999)


def _ridge_ponderado(X: np.ndarray, y: np.ndarray, w: np.ndarray, alpha: float) -> np.ndarray:
    """Ridge com intercepto não penalizado. X já padronizado."""
    Xi = np.column_stack([np.ones(len(X)), X])
    P = alpha * np.eye(Xi.shape[1])
    P[0, 0] = 0.0
    W = w / w.sum() * len(w)
    A = Xi.T @ (Xi * W[:, None]) + P
    return np.linalg.solve(A, Xi.T @ (W * y))


@dataclass
class Projecao:
    pct_a: float  # % projetado de A nos votos válidos (0-100)
    ic90: tuple[float, float]
    prob_a_vence: float
    pct_a_parcial: float  # o que o placar parcial mostra agora
    pct_votos_apurados: float  # fração estimada dos votos válidos já apurados (0-100)
    votos_validos_projetados: float
    por_municipio: pd.DataFrame

    def resumo(self, nome_a: str = "A", nome_b: str = "B") -> str:
        return (
            f"apurado ~{self.pct_votos_apurados:.1f}% | parcial {nome_a} {self.pct_a_parcial:.2f}% | "
            f"projeção {nome_a} {self.pct_a:.2f}% (IC90 {self.ic90[0]:.2f}-{self.ic90[1]:.2f}) | "
            f"P({nome_a} vence) {100 * self.prob_a_vence:.1f}% / P({nome_b}) {100 * (1 - self.prob_a_vence):.1f}%"
        )


def projetar(
    t1: pd.DataFrame,
    t2_parcial: pd.DataFrame,
    a: str,
    b: str,
    alpha: float = 5.0,
    min_fracao: float = 0.0,
    n_sim: int = 4000,
    seed: int = 0,
) -> Projecao:
    """Projeta o resultado final do 2T.

    t1: resultado final do 1T por município (formato largo, dados.py).
    t2_parcial: estado atual do 2T por município (mesmo formato; municípios sem
        dados podem estar ausentes ou com pct_secoes_totalizadas = 0).
    a, b: números dos finalistas (ex.: "13", "22").
    alpha: força do encolhimento em direção ao palpite inicial.
    min_fracao: só usa no ajuste municípios com ao menos esta fração apurada.
    """
    chave = ["uf", "cd_municipio_tse"]
    base = t1.set_index(chave)
    x = atributos_1t(base.reset_index(), a, b).set_index(base.index)
    prior = palpite_inicial(x)

    cols2 = ["pct_secoes_totalizadas", "votos_validos", f"v_{a}", f"v_{b}", "comparecimento"]
    t2 = t2_parcial.set_index(chave).reindex(base.index)
    t2 = t2.reindex(columns=cols2).fillna(0.0)
    f = (t2["pct_secoes_totalizadas"] / 100).clip(0, 1)
    obs_a, obs_b = t2[f"v_{a}"], t2[f"v_{b}"]
    obs_vv = obs_a + obs_b

    # --- quantos votos válidos cada município terá no 2T -------------------
    vv1 = base["votos_validos"].clip(lower=1)
    tem = (f > 0.05) & (obs_vv > 0)
    razao_obs = (obs_vv / f.where(tem)) / vv1
    if tem.sum() >= 5:
        razao_padrao = float(np.average(razao_obs[tem].clip(0.5, 1.5), weights=vv1[tem]))
    else:
        razao_padrao = 1.0
    # municípios apurados: extrapola a própria razão (encolhida p/ média conforme f)
    razao = np.where(tem, f * razao_obs.fillna(razao_padrao) + (1 - f) * razao_padrao, razao_padrao)
    vv_final = np.maximum(vv1 * razao, obs_vv)
    restante = np.maximum(vv_final - obs_vv, 0.0)

    # --- ajuste "1T -> 2T" nos municípios já apurados ----------------------
    usar = (obs_vv > 0) & (f >= min_fracao)
    feats = x.drop(columns=[c for c in x.columns if c.startswith("r_")]).columns.tolist()
    reg = [c for c in x.columns if c.startswith("r_")]
    Xall = x[feats + reg].to_numpy(float)
    mu, sd = Xall.mean(0), Xall.std(0) + 1e-9
    Z = (Xall - mu) / sd

    logit = lambda p: np.log(p / (1 - p))  # noqa: E731
    ilogit = lambda z: 1 / (1 + np.exp(-z))  # noqa: E731
    lp = logit(prior.to_numpy())
    pred = prior.to_numpy().copy()
    sigma_mu, sigma_uf = 0.08, 0.15  # em logit, valores conservadores p/ início da noite
    eta = pd.Series(0.0, index=sorted(set(base.index.get_level_values("uf"))))
    var_eta = pd.Series(sigma_uf**2, index=eta.index)

    if usar.sum() >= 3:
        y = logit((obs_a[usar] / obs_vv[usar]).clip(0.001, 0.999).to_numpy())
        w = obs_vv[usar].to_numpy()
        coef = _ridge_ponderado(Z[usar.to_numpy()], y - lp[usar.to_numpy()], w, alpha)
        z_pred = lp + coef[0] + Z @ coef[1:]
        pred = ilogit(z_pred)

        # efeitos de UF sobre os resíduos, com encolhimento
        res = pd.Series(y - z_pred[usar.to_numpy()], index=obs_vv[usar].index)
        ufs = res.index.get_level_values("uf")
        wr = pd.Series(w, index=res.index)
        media_uf = (res * wr).groupby(ufs).sum() / wr.groupby(ufs).sum()
        n_uf = res.groupby(ufs).size()
        resid_mu = res - media_uf.reindex(ufs).to_numpy()
        sigma_mu = float(np.sqrt(np.average(resid_mu**2, weights=w))) if len(w) > 10 else sigma_mu
        if len(media_uf) >= 4:
            sigma_uf = float(max(media_uf.std(), 0.02))
        lam = n_uf * sigma_uf**2 / (n_uf * sigma_uf**2 + sigma_mu**2)
        eta.loc[media_uf.index] = lam * media_uf
        var_eta.loc[media_uf.index] = (1 - lam) * sigma_uf**2

    # --- simulação ----------------------------------------------------------
    uf_idx = pd.Index(eta.index).get_indexer(base.index.get_level_values("uf"))
    z_base = logit(np.clip(pred, 0.001, 0.999)) + eta.to_numpy()[uf_idx]
    n_ufs_obs = max(1, int((var_eta < sigma_uf**2 - 1e-12).sum()))
    sigma_nat = sigma_uf / np.sqrt(n_ufs_obs)

    rng = np.random.default_rng(seed)
    choque_uf = rng.normal(0, 1, (n_sim, len(eta))) * np.sqrt(var_eta.to_numpy())
    choque_nat = rng.normal(0, sigma_nat, (n_sim, 1))
    choque_mu = rng.normal(0, sigma_mu, (n_sim, len(base))) * 0.3  # muito diluído na soma
    p_sim = ilogit(z_base[None, :] + choque_uf[:, uf_idx] + choque_nat + choque_mu)

    rest = np.asarray(restante, dtype=float)
    total_a = obs_a.sum() + p_sim @ rest
    total = obs_vv.sum() + rest.sum()
    pct_sim = 100 * total_a / total

    por_mu = pd.DataFrame({
        "fracao_apurada": f,
        "pct_a_1t": 100 * x["s_a"],
        "pct_a_parcial": 100 * (obs_a / obs_vv.where(obs_vv > 0)),
        "pct_a_previsto_restante": 100 * ilogit(z_base),
        "votos_validos_restantes": restante,
    })
    return Projecao(
        pct_a=float(pct_sim.mean()),
        ic90=(float(np.percentile(pct_sim, 5)), float(np.percentile(pct_sim, 95))),
        prob_a_vence=float((pct_sim > 50).mean()),
        pct_a_parcial=float(100 * obs_a.sum() / obs_vv.sum()) if obs_vv.sum() > 0 else float("nan"),
        pct_votos_apurados=float(100 * obs_vv.sum() / total),
        votos_validos_projetados=float(total),
        por_municipio=por_mu,
    )
