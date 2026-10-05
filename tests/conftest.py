import numpy as np
import pandas as pd
import pytest

from deolhonovoto.nowcast import REGIAO


def _sintetico(n=3000, seed=1):
    """1T e 2T sintéticos com transferência de votos e efeito regional conhecidos."""
    rng = np.random.default_rng(seed)
    ufs = rng.choice([u for u in REGIAO if u != "ZZ"], n)
    regiao_ne = np.isin([REGIAO[u] for u in ufs], ["NE", "N"])
    eleit = np.round(np.exp(rng.normal(9.5, 1.2, n))) + 1000
    comp = np.round(eleit * rng.uniform(0.7, 0.85, n))
    bn = np.round(comp * rng.uniform(0.02, 0.06, n))
    vv = comp - bn
    s_a = np.clip(0.42 + 0.25 * regiao_ne + rng.normal(0, 0.08, n), 0.05, 0.9)
    s_o = rng.uniform(0.03, 0.12, n)
    s_b = np.clip(1 - s_a - s_o, 0.01, None)
    t1 = pd.DataFrame({
        "uf": ufs, "cd_municipio_tse": [f"{i:05d}" for i in range(n)],
        "eleitorado": eleit, "comparecimento": comp, "votos_brancos": bn / 2, "votos_nulos": bn / 2,
        "votos_validos": vv, "v_13": np.round(vv * s_a), "v_22": np.round(vv * s_b),
        "v_99": np.round(vv * s_o), "pct_secoes_totalizadas": 100.0,
    })
    # 2T: A herda 35% dos "outros" + choque regional
    choque = {u: rng.normal(0, 0.01) for u in REGIAO}
    p2 = np.clip(s_a + 0.35 * s_o + np.array([choque[u] for u in ufs]) + rng.normal(0, 0.01, n), 0.01, 0.99)
    vv2 = np.round(vv * rng.uniform(0.95, 1.02, n))
    t2 = t1.copy()
    t2["votos_validos"] = vv2
    t2["v_13"] = np.round(vv2 * p2)
    t2["v_22"] = vv2 - t2["v_13"]
    t2 = t2.drop(columns=["v_99"])
    return t1, t2


@pytest.fixture
def sintetico():
    return _sintetico()
