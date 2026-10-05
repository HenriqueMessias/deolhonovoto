"""Cliente para os JSONs públicos de divulgação de resultados do TSE.

O site https://resultados.tse.jus.br/oficial/app/ é uma SPA que lê arquivos JSON
estáticos servidos pela CDN do próprio TSE. Os caminhos seguem o padrão:

    {BASE}/comum/config/ele-c.json
        -> pleitos e eleições disponíveis (códigos de eleição, turnos, datas)
    {BASE}/{ciclo}/{eleicao}/config/mun-e{eleicao:06d}-cm.json
        -> lista de UFs e municípios (código TSE, código IBGE, nome, zonas)
    {BASE}/{ciclo}/{eleicao}/dados-simplificados/{uf}/{arquivo}-c{cargo:04d}-e{eleicao:06d}-r.json
        -> totais da abrangência (BR, UF ou município) + votos por candidato

onde `ciclo` é "ele2026", `uf` é minúscula ("br", "sp", "zz" = exterior) e o
arquivo é "br", "sp" ou "sp71072" (UF + código TSE do município).

Os números chegam como strings e os percentuais com vírgula decimal ("48,43").
O parser abaixo é tolerante: campos ausentes viram NaN em vez de quebrar a coleta.
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

# Campos de totais do arquivo "-r.json" (nome no JSON -> nome legível).
CAMPOS_TOTAIS = {
    "s": "secoes",
    "st": "secoes_totalizadas",
    "pst": "pct_secoes_totalizadas",
    "e": "eleitorado",
    "ea": "eleitorado_apurado",
    "pea": "pct_eleitorado_apurado",
    "c": "comparecimento",
    "pc": "pct_comparecimento",
    "a": "abstencao",
    "pa": "pct_abstencao",
    "vv": "votos_validos",
    "pvv": "pct_votos_validos",
    "vb": "votos_brancos",
    "pvb": "pct_votos_brancos",
    "tvn": "votos_nulos",
    "ptvn": "pct_votos_nulos",
    "van": "votos_anulados",
    "tv": "total_votos",
}

CAMPOS_CANDIDATO = {
    "seq": "seq",
    "sqcand": "sq_candidato",
    "n": "numero",
    "nm": "nome",
    "cc": "coligacao",
    "e": "eleito",
    "st": "situacao",
    "dvt": "destino_voto",
    "vap": "votos",
    "pvap": "pct_votos_validos",
}


def to_number(valor: Any) -> float:
    """Converte "1.234", "48,43" ou "" do TSE para float (NaN se vazio)."""
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

    def url_municipios(self, base: str = BASE_URL) -> str:
        return f"{base}/{self.ciclo}/{self.codigo}/config/mun-{self.sufixo}-cm.json"

    def url_resultado(self, abrangencia: str, cargo: int = CARGO_PRESIDENTE, base: str = BASE_URL) -> str:
        """URL do arquivo simplificado.

        `abrangencia`: "br", uma UF ("sp", "zz") ou UF + código TSE do município ("sp71072").
        """
        abrangencia = abrangencia.lower()
        uf = abrangencia[:2]
        return (
            f"{base}/{self.ciclo}/{self.codigo}/dados-simplificados/{uf}/"
            f"{abrangencia}-c{cargo:04d}-{self.sufixo}-r.json"
        )


def url_config(base: str = BASE_URL) -> str:
    return f"{base}/comum/config/ele-c.json"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_resultado(payload: dict) -> tuple[dict, list[dict]]:
    """Separa um "-r.json" em (totais da abrangência, lista de candidatos)."""
    totais: dict[str, Any] = {
        "eleicao": payload.get("ele"),
        "tipo_abrangencia": payload.get("tpabr"),
        "abrangencia": payload.get("cdabr"),
        "turno": payload.get("t"),
        "data_geracao": payload.get("dg"),
        "hora_geracao": payload.get("hg"),
        "data_totalizacao": payload.get("dt"),
        "hora_totalizacao": payload.get("ht"),
        "matematicamente_definida": payload.get("md"),
    }
    for chave, nome in CAMPOS_TOTAIS.items():
        totais[nome] = to_number(payload.get(chave))

    candidatos = []
    for cand in payload.get("cand", []) or []:
        linha: dict[str, Any] = {}
        for chave, nome in CAMPOS_CANDIDATO.items():
            valor = cand.get(chave)
            linha[nome] = to_number(valor) if nome in ("votos", "pct_votos_validos") else valor
        candidatos.append(linha)
    return totais, candidatos


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
                "capital": mu.get("c") == "S",
                "zonas": ",".join(mu.get("z", []) or []),
            }


def iter_eleicoes(config: dict) -> Iterator[dict]:
    """Extrai as eleições listadas em ele-c.json (pleito, código, turno, data)."""
    for pleito in config.get("pl", []) or []:
        for ele in pleito.get("e", []) or []:
            yield {
                "pleito": pleito.get("cd"),
                "data": pleito.get("dt") or ele.get("dt"),
                "eleicao": ele.get("cd"),
                "turno": ele.get("t"),
                "tipo": ele.get("tp"),
                "nome": ele.get("nm") or ele.get("nmabr"),
                "bruto": json.dumps(ele, ensure_ascii=False),
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
        self._http = httpx.AsyncClient(
            timeout=timeout,
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
                    resp = await self._http.get(url, headers=headers)
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
            except (httpx.TransportError, httpx.HTTPStatusError):
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

    async def resultados(
        self, eleicao: Eleicao, abrangencias: Iterable[str], cargo: int = CARGO_PRESIDENTE
    ) -> list[tuple[str, dict]]:
        """Baixa em paralelo vários "-r.json"; devolve só os que mudaram."""
        abrangencias = list(abrangencias)
        urls = [eleicao.url_resultado(a, cargo, self.base) for a in abrangencias]
        respostas = await asyncio.gather(*(self.get_json(u) for u in urls), return_exceptions=True)
        saida = []
        for abr, resp in zip(abrangencias, respostas):
            if isinstance(resp, BaseException):
                continue
            payload, mudou = resp
            if payload is not None and mudou:
                saida.append((abr, payload))
        return saida
