"""Leitura somente-leitura dos arquivos de `dados/`.

Estes arquivos sao o estado inicial do condominio e nunca sao escritos:
sao carregados na memoria do processo e servem de catalogo (apartamentos,
areas) ou de fonte para a restauracao do banco.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import DADOS_DIR


def _ler_json(nome: str) -> list[dict[str, Any]]:
    with open(DADOS_DIR / nome, encoding="utf-8") as arquivo:
        return json.load(arquivo)


@lru_cache(maxsize=1)
def apartamentos() -> dict[str, str]:
    """Mapa `numero -> morador`."""
    return {item["numero"]: item["morador"] for item in _ler_json("apartamentos.json")}


@lru_cache(maxsize=1)
def areas() -> dict[str, dict[str, Any]]:
    """Mapa `id da area -> {id, nome, taxa}`."""
    return {item["id"]: item for item in _ler_json("areas.json")}


def reservas_iniciais() -> list[dict[str, Any]]:
    return _ler_json("reservas.json")


def visitantes_iniciais() -> list[dict[str, Any]]:
    return _ler_json("visitantes.json")


def apartamento_existe(numero: str) -> bool:
    return numero in apartamentos()


def morador_de(numero: str) -> str | None:
    return apartamentos().get(numero)


def texto_regulamento() -> str:
    with open(DADOS_DIR / "regulamento.md", encoding="utf-8") as arquivo:
        return arquivo.read()
