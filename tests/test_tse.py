import math

import pandas as pd

from deolhonovoto.coletor import gravar_rodada
from deolhonovoto.dados import estado_municipal
from deolhonovoto.tse import Eleicao, iter_municipios, parse_resultado, to_number

PAYLOAD = {
    "ele": "6257", "tpabr": "MU", "cdabr": "71072", "t": "1", "dg": "04/10/2026", "hg": "21:00:00",
    "s": "1000", "st": "500", "pst": "50,00", "e": "9.000.000", "c": "7000", "vv": "6000",
    "vb": "500", "tvn": "500",
    "cand": [
        {"seq": "1", "sqcand": "1", "n": "13", "nm": "FULANO", "vap": "4000", "pvap": "66,67"},
        {"seq": "2", "sqcand": "2", "n": "22", "nm": "CICLANO", "vap": "2000", "pvap": "33,33"},
    ],
}


def test_to_number():
    assert to_number("48,43") == 48.43
    assert to_number("1.234,5") == 1234.5
    assert to_number("123") == 123
    assert math.isnan(to_number(""))


def test_urls():
    e = Eleicao("ele2026", 6257)
    assert e.url_resultado("br").endswith("/ele2026/6257/dados-simplificados/br/br-c0001-e006257-r.json")
    assert e.url_resultado("SP71072").endswith("/dados-simplificados/sp/sp71072-c0001-e006257-r.json")
    assert e.url_municipios().endswith("/ele2026/6257/config/mun-e006257-cm.json")


def test_parse_resultado():
    totais, cands = parse_resultado(PAYLOAD)
    assert totais["pct_secoes_totalizadas"] == 50.0
    assert totais["eleitorado"] == 9_000_000
    assert [c["votos"] for c in cands] == [4000, 2000]


def test_iter_municipios():
    payload = {"abr": [{"cd": "SP", "mu": [{"cd": "71072", "cdi": "3550308", "nm": "SÃO PAULO", "c": "S", "z": ["0001"]}]}]}
    (m,) = list(iter_municipios(payload))
    assert m["uf"] == "SP" and m["capital"] and m["cd_municipio_tse"] == "71072"


def test_roundtrip_estado_municipal(tmp_path):
    e = Eleicao("ele2026", 6257)
    gravar_rodada(tmp_path, e, 1, "20261004T220000Z", [("sp71072", PAYLOAD)])
    novo = dict(PAYLOAD, pst="100,00", cand=[dict(PAYLOAD["cand"][0], vap="8000"), PAYLOAD["cand"][1]])
    gravar_rodada(tmp_path, e, 1, "20261004T230000Z", [("sp71072", novo)])

    df = estado_municipal(tmp_path, 6257)
    assert len(df) == 1 and df.loc[0, "v_13"] == 8000 and df.loc[0, "uf"] == "SP"
    antigo = estado_municipal(tmp_path, 6257, ate="20261004T220000Z")
    assert antigo.loc[0, "v_13"] == 4000
    assert (tmp_path / "raw/ele2026/6257/c0001/20261004T220000Z/sp71072.json.gz").exists()
