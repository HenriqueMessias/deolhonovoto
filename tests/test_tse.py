import json
import math
from pathlib import Path

from deolhonovoto.coletor import gravar_progresso, gravar_rodada
from deolhonovoto.dados import estado_municipal
from deolhonovoto.tse import Eleicao, iter_eleicoes, iter_municipios, parse_progresso, parse_resultado, to_number

FIXTURES = Path(__file__).parent / "fixtures"
# Arquivos reais do TSE (1º turno 2026, São Paulo/SP e progresso do Acre).
SP = json.loads((FIXTURES / "sp71072-c0001-e006257-u.json").read_text(encoding="utf-8"))
AC_AB = json.loads((FIXTURES / "ac-e006257-ab.json").read_text(encoding="utf-8"))


def test_to_number():
    assert to_number("48,43") == 48.43
    assert to_number("47,027772356") == 47.027772356
    assert to_number("1.234,5") == 1234.5
    assert to_number("9.000.000") == 9_000_000
    assert to_number("123") == 123
    assert math.isnan(to_number(""))


def test_urls():
    e = Eleicao("ele2026", 6257)
    assert e.url_resultado("br").endswith("/ele2026/6257/dados/br/br-c0001-e006257-u.json")
    assert e.url_resultado("SP71072").endswith("/ele2026/6257/dados/sp/sp71072-c0001-e006257-u.json")
    assert e.url_progresso("SP").endswith("/ele2026/6257/dados/sp/sp-e006257-ab.json")
    assert e.url_municipios().endswith("/ele2026/6257/config/mun-e006257-cm.json")


def test_parse_resultado_real():
    totais, cands = parse_resultado(SP, cargo=1)
    assert totais["abrangencia"] == "71072"
    assert totais["pct_secoes_totalizadas"] == 100.0
    assert totais["votos_validos"] == sum(c["votos"] for c in cands)
    assert totais["comparecimento"] == totais["votos_validos"] + totais["votos_brancos"] + totais["votos_nulos"]
    lula = next(c for c in cands if c["numero"] == "13")
    assert lula["nome"] == "LULA" and lula["votos"] > 0 and 0 < lula["pct_votos_validos"] < 100
    assert parse_resultado(SP, cargo=3)[1] == []  # filtro de cargo


def test_parse_progresso_real():
    linhas = parse_progresso(AC_AB)
    mun = [l for l in linhas if l["tipo_abrangencia"] == "mun"]
    assert len(mun) == 22  # municípios do Acre
    assert all(l["secoes_totalizadas"] <= l["secoes"] for l in mun)
    assert all(l["hora_totalizacao"] for l in mun)


def test_iter_municipios_e_eleicoes():
    payload = {"abr": [{"cd": "sp", "mu": [{"cd": "71072", "cdi": "3550308", "nm": "SÃO PAULO", "c": "s", "z": ["0001"]}]}]}
    (m,) = list(iter_municipios(payload))
    assert m["uf"] == "SP" and m["capital"] and m["cd_municipio_tse"] == "71072"
    config = {"pl": [{"cd": "3220", "c": "ele2026", "dt": "04/10/2026", "e": [
        {"cd": "6257", "cdt2": "6258", "t": "1", "nm": "Federal", "abr": [{"cd": "br", "cp": [{"cd": "1", "ds": "Presidente"}]}]}]}]}
    (e,) = list(iter_eleicoes(config))
    assert e["eleicao_2t"] == "6258" and e["cargos"] == "Presidente" and e["ciclo"] == "ele2026"


def test_roundtrip_estado_municipal(tmp_path):
    e = Eleicao("ele2026", 6257)
    parcial = json.loads(json.dumps(SP))
    parcial["s"]["pstn"] = "50"
    gravar_rodada(tmp_path, e, 1, "20261004T220000Z", [("sp71072", parcial)])
    gravar_rodada(tmp_path, e, 1, "20261004T230000Z", [("sp71072", SP)])
    gravar_progresso(tmp_path, e, "20261004T230000Z", [("ac", AC_AB)])

    df = estado_municipal(tmp_path, 6257)
    assert len(df) == 1 and df.loc[0, "uf"] == "SP" and df.loc[0, "pct_secoes_totalizadas"] == 100
    assert df.loc[0, "v_13"] > 0
    antigo = estado_municipal(tmp_path, 6257, ate="20261004T220000Z")
    assert antigo.loc[0, "pct_secoes_totalizadas"] == 50
    assert (tmp_path / "raw/ele2026/6257/c0001/20261004T220000Z/sp71072.json.gz").exists()
    assert (tmp_path / "processed/6257/progresso/20261004T230000Z.parquet").exists()
