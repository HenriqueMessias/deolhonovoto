from deolhonovoto.nowcast import projetar
from deolhonovoto.simulacao import backtest, estado_parcial, inicio_apuracao


def test_apuracao_completa_bate_com_final(sintetico):
    t1, t2 = sintetico
    p = projetar(t1, t2, "13", "22")
    final = 100 * t2.v_13.sum() / (t2.v_13.sum() + t2.v_22.sum())
    assert abs(p.pct_a - final) < 1e-6
    assert p.pct_votos_apurados > 99.99


def test_sem_dados_usa_prior(sintetico):
    t1, t2 = sintetico
    vazio = estado_parcial(t2, inicio_apuracao(t2), tau=0.0)
    p = projetar(t1, vazio, "13", "22")
    assert p.ic90[1] - p.ic90[0] > 1  # incerteza grande sem dados


def test_projecao_corrige_vies_da_parcial(sintetico):
    t1, t2 = sintetico
    bt = backtest(t1, t2, "13", "22", taus=(0.2, 0.3, 0.4, 0.5))
    meio = bt[(bt.pct_apurado > 10) & (bt.pct_apurado < 90)]
    assert len(meio) >= 2
    assert (meio.erro_projecao.abs() < meio.erro_parcial.abs()).all()
    assert (meio.erro_projecao.abs() < 1.0).all()
    # o valor final cai dentro do IC90 na maior parte dos pontos
    assert ((meio.final_a >= meio.ic90_inf) & (meio.final_a <= meio.ic90_sup)).mean() >= 0.5


def test_estado_real_soma_zonas_totalizadas():
    import pandas as pd

    from deolhonovoto.simulacao import estado_real

    zonas = pd.DataFrame({
        "uf": ["SP", "SP", "AC"], "cd_municipio_tse": ["71072", "71072", "01120"], "zona": ["0001", "0002", "0008"],
        "eleitorado": [100, 300, 50], "comparecimento": [80, 240, 40], "votos_brancos": [1, 2, 1],
        "votos_nulos": [1, 2, 1], "votos_validos": [78, 236, 38], "v_13": [40, 100, 30], "v_22": [38, 136, 8],
        "totalizado_em": pd.to_datetime(["2022-10-30 18:00", "2022-10-30 20:00", "2022-10-30 19:00"]),
    })
    df = estado_real(zonas, pd.Timestamp("2022-10-30 19:30")).set_index("cd_municipio_tse")
    assert df.loc["71072", "v_13"] == 40 and df.loc["71072", "pct_secoes_totalizadas"] == 25
    assert df.loc["01120", "v_22"] == 8 and df.loc["01120", "pct_secoes_totalizadas"] == 100
    assert df.loc["71072", "eleitorado"] == 400
