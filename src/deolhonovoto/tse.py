"""Cliente para os JSONs públicos de divulgação de resultados do TSE.

O site https://resultados.tse.jus.br/oficial/app/ é uma SPA que lê arquivos JSON
estáticos servidos pela CDN do próprio TSE. Caminhos (validados em 05/10/2026):

    {BASE}/comum/config/ele-c.json
        -> pleitos e eleições (código, turno, `cdt2` = código do 2º turno)
    {BASE}/{ciclo}/{eleicao}/config/mun-e{eleicao:06d}-cm.json
        -> UFs e municípios (código TSE, código IBGE, nome, capital, zonas)
    {BASE}/{ciclo}/{eleicao}/dados/{uf}/{uf}-e{eleicao:06d}-ab.json
        -> progresso de cada município da UF (seções totalizadas, hora); leve,
           serve para saber quais municípios mudaram desde a última rodada
    {BASE}/{ciclo}/{eleicao}/dados/{uf}/{abr}-c{cargo:04d}-e{eleicao:06d}-u.json
        -> totais + votos por candidato da abrangência

onde `ciclo` é "ele2026", `uf` é minúscula ("br", "sp", "zz" = exterior) e `abr`
é "br", "sp" ou "sp71072" (UF + código TSE do município).

Os números chegam como strings, com vírgula decimal ("47,03"). O parser é
tolerante: campos ausentes viram NaN em vez de quebrar a coleta.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import httpx

BASE_URL = "https://resultados.tse.jus.br/oficial"

_MILHAR = re.compile(r"-?\d{1,3}(\.\d{3})+")

CARGO_PRESIDENTE = 1
CARGO_GOVERNADOR = 3
CARGO_SENADOR = 5

# Totais do "-u.json": (bloco, chave no JSON) -> nome legível.
CAMPOS_TOTAIS = {
    ("s", "ts"): "secoes",
    ("s", "st"): "secoes_totalizadas",
    ("s", "pstn"): "pct_secoes_totalizadas",
    ("e", "te"): "eleitorado",
    ("e", "est"): "eleitorado_totalizado",
    ("e", "c"): "comparecimento",
    ("e", "a"): "abstencao",
    ("v", "tv"): "total_votos",
    ("v", "vv"): "votos_validos",
    ("v", "vb"): "votos_brancos",
    ("v", "tvn"): "votos_nulos",
    ("v", "van"): "votos_anulados",
}

CAMPOS_CANDIDATO = {
    "seq": "seq",
    "sqcand": "sq_candidato",
    "n": "numero",
    "nmu": "nome",
    "nm": "nome_completo",
    "e": "eleito",
    "st": "situacao",
    "dvt": "destino_voto",
    "vap": "votos",
    "pvapn": "pct_votos_validos",
}
CAMPOS_NUMERICOS = {"votos", "pct_votos_validos"}


def to_number(valor: Any) -> float:
    """Converte "1.234", "47,027" ou "" do TSE para float (NaN se vazio)."""
    if valor is None:
        return math.nan
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    if not texto:
        return math.nan
    if "," in texto or _MILHAR.fullmatch(texto):  # pt-BR: ponto = milhar, vírgula = decimal
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return math.nan


@dataclass(frozen=True)
class Eleicao:
    """Identifica uma eleição no site de resultados (ex.: ele2026 / 6257)."""

    ciclo: str  # "ele2026"
    codigo: int  # código da eleição, ex.: 6257 (aparece na URL do app)

    @property
    def sufixo(self) -> str:
        return f"e{self.codigo:06d}"

    def _dados(self, uf: str, base: str) -> str:
        return f"{base}/{self.ciclo}/{self.codigo}/dados/{uf.lower()}"

    def url_municipios(self, base: str = BASE_URL) -> str:
        return f"{base}/{self.ciclo}/{self.codigo}/config/mun-{self.sufixo}-cm.json"

    def url_progresso(self, uf: str, base: str = BASE_URL) -> str:
        """Progresso da apuração por município de uma UF ("-ab.json")."""
        return f"{self._dados(uf, base)}/{uf.lower()}-{self.sufixo}-ab.json"

    def url_resultado(self, abrangencia: str, cargo: int = CARGO_PRESIDENTE, base: str = BASE_URL) -> str:
        """`abrangencia`: "br", uma UF ("sp", "zz") ou UF + código TSE do município ("sp71072")."""
        abrangencia = abrangencia.lower()
        return f"{self._dados(abrangencia[:2], base)}/{abrangencia}-c{cargo:04d}-{self.sufixo}-u.json"


def url_config(base: str = BASE_URL) -> str:
    return f"{base}/comum/config/ele-c.json"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _iter_candidatos(payload: dict, cargo: int | None) -> Iterator[dict]:
    """Candidatos ficam em carg[].agr[] (coligação/partido isolado) -> par[] -> cand[]."""
    for carg in payload.get("carg", []) or []:
        if cargo is not None and str(carg.get("cd")) != str(cargo):
            continue
        for agr in carg.get("agr", []) or []:
            for par in agr.get("par", []) or []:
                for cand in par.get("cand", []) or []:
                    yield {**cand, "_partido": par.get("sg"), "_agremiacao": agr.get("nm")}


def parse_resultado(payload: dict, cargo: int | None = None) -> tuple[dict, list[dict]]:
    """Separa um "-u.json" em (totais da abrangência, lista de candidatos)."""
    totais: dict[str, Any] = {
        "eleicao": payload.get("ele"),
        "tipo_abrangencia": payload.get("tpabr"),
        "abrangencia": payload.get("cdabr"),
        "turno": payload.get("t"),
        "data_geracao": payload.get("dg"),
        "hora_geracao": payload.get("hg"),
        "data_totalizacao": payload.get("dt"),
        "hora_totalizacao": payload.get("ht"),
        "totalizacao_final": payload.get("tf"),
        "matematicamente_definida": payload.get("md"),
    }
    for (bloco, chave), nome in CAMPOS_TOTAIS.items():
        totais[nome] = to_number((payload.get(bloco) or {}).get(chave))

    candidatos = []
    for cand in _iter_candidatos(payload, cargo):
        linha: dict[str, Any] = {nome: cand.get(chave) for chave, nome in CAMPOS_CANDIDATO.items()}
        for nome in CAMPOS_NUMERICOS:
            linha[nome] = to_number(linha[nome])
        linha["partido"] = cand["_partido"]
        linha["agremiacao"] = cand["_agremiacao"]
        candidatos.append(linha)
    return totais, candidatos


def parse_progresso(payload: dict) -> list[dict]:
    """Linhas do "-ab.json": uma por abrangência (UF ou município) com o progresso."""
    linhas = []
    for abr in payload.get("abr", []) or []:
        s, e = abr.get("s") or {}, abr.get("e") or {}
        linhas.append({
            "tipo_abrangencia": abr.get("tpabr"),
            "abrangencia": abr.get("cdabr"),
            "data_totalizacao": abr.get("dt"),
            "hora_totalizacao": abr.get("ht"),
            "secoes": to_number(s.get("ts")),
            "secoes_totalizadas": to_number(s.get("st")),
            "pct_secoes_totalizadas": to_number(s.get("pstn")),
            "eleitorado": to_number(e.get("te")),
            "comparecimento": to_number(e.get("c")),
        })
    return linhas


def iter_municipios(payload: dict) -> Iterator[dict]:
    """Percorre o "mun-eXXXXXX-cm.json" e devolve um dict por município."""
    for uf in payload.get("abr", []) or []:
        sigla = str(uf.get("cd", "")).upper()
        for mu in uf.get("mu", []) or []:
            yield {
                "uf": sigla,
                "cd_municipio_tse": str(mu.get("cd")),
                "cd_municipio_ibge": mu.get("cdi"),
                "nome": mu.get("nm"),
                "capital": str(mu.get("c", "")).lower() == "s",
                "zonas": ",".join(mu.get("z", []) or []),
            }


def iter_eleicoes(config: dict) -> Iterator[dict]:
    """Extrai as eleições listadas em ele-c.json (ciclo, código, turno, código do 2T)."""
    for pleito in config.get("pl", []) or []:
        for ele in pleito.get("e", []) or []:
            cargos = [cp.get("ds") for abr in ele.get("abr", []) or [] for cp in abr.get("cp", []) or []]
            yield {
                "ciclo": pleito.get("c"),
                "pleito": pleito.get("cd"),
                "data": pleito.get("dt"),
                "eleicao": ele.get("cd"),
                "turno": ele.get("t"),
                "eleicao_2t": ele.get("cdt2"),
                "nome": ele.get("nm"),
                "cargos": ", ".join(dict.fromkeys(c for c in cargos if c)),
            }


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------


class ClienteTSE:
    """Cliente HTTP assíncrono com cache por ETag/Last-Modified.

    O cache evita baixar de novo arquivos que não mudaram entre duas rodadas de
    coleta, o que reduz bastante a carga na CDN do TSE durante a apuração.
    """

    def __init__(self, base: str = BASE_URL, concorrencia: int = 16, timeout: float = 30.0):
        self.base = base
        self._sem: asyncio.Semaphore | None = None
        self._concorrencia = concorrencia
        self._validadores: dict[str, dict[str, str]] = {}
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=concorrencia, max_keepalive_connections=concorrencia),
            follow_redirects=True,
            headers={"User-Agent": "deolhonovoto/0.1 (+https://github.com/HenriqueMessias/deolhonovoto)"},
        )

    async def __aenter__(self) -> "ClienteTSE":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def get_json(self, url: str, tentativas: int = 4) -> tuple[dict | None, bool]:
        """Baixa um JSON. Retorna (payload, mudou). payload=None se 404/304."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._concorrencia)
        headers = {}
        validador = self._validadores.get(url, {})
        if "etag" in validador:
            headers["If-None-Match"] = validador["etag"]
        if "last-modified" in validador:
            headers["If-Modified-Since"] = validador["last-modified"]

        espera = 1.0
        for tentativa in range(tentativas):
            try:
                async with self._sem:
                    # prazo total: o timeout do httpx é por operação e não pega
                    # conexões que ficam pingando bytes ou presas no pool
                    resp = await asyncio.wait_for(self._http.get(url, headers=headers), self._timeout)
                if resp.status_code == 304:
                    return None, False
                if resp.status_code == 404:
                    return None, False
                resp.raise_for_status()
                self._validadores[url] = {
                    k: v for k, v in (("etag", resp.headers.get("etag")),
                                      ("last-modified", resp.headers.get("last-modified"))) if v
                }
                return resp.json(), True
            except (httpx.TransportError, httpx.HTTPStatusError, asyncio.TimeoutError):
                if tentativa == tentativas - 1:
                    raise
                await asyncio.sleep(espera)
                espera *= 2
        return None, False

    async def config(self) -> dict | None:
        payload, _ = await self.get_json(url_config(self.base))
        return payload

    async def municipios(self, eleicao: Eleicao) -> list[dict]:
        payload, _ = await self.get_json(eleicao.url_municipios(self.base))
        return list(iter_municipios(payload or {}))

    async def progresso(self, eleicao: Eleicao, ufs: Iterable[str]) -> list[tuple[str, dict]]:
        """Baixa os "-ab.json" das UFs; devolve só os que mudaram."""
        ufs = list(ufs)
        respostas = await asyncio.gather(
            *(self.get_json(eleicao.url_progresso(uf, self.base)) for uf in ufs), return_exceptions=True
        )
        return [(uf, r[0]) for uf, r in zip(ufs, respostas)
                if not isinstance(r, BaseException) and r[0] is not None]

    async def resultados(
        self, eleicao: Eleicao, abrangencias: Iterable[str], cargo: int = CARGO_PRESIDENTE
    ) -> tuple[list[tuple[str, dict]], list[str]]:
        """Baixa em paralelo vários "-u.json".

        Retorna (arquivos que mudaram, abrangências que falharam após as retentativas).
        """
        abrangencias = list(abrangencias)
        urls = [eleicao.url_resultado(a, cargo, self.base) for a in abrangencias]
        respostas = await asyncio.gather(*(self.get_json(u) for u in urls), return_exceptions=True)
        saida, falhas = [], []
        for abr, resp in zip(abrangencias, respostas):
            if isinstance(resp, BaseException):
                falhas.append(abr)
                continue
            payload, mudou = resp
            if payload is not None and mudou:
                saida.append((abr, payload))
        return saida, falhas
