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
