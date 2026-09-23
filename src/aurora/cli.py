"""Comandos do projeto: subir a API e restaurar os dados iniciais."""

from __future__ import annotations

import asyncio
import os

import uvicorn

from . import dados, storage
from .config import caminho_banco_condominio


def subir_api() -> None:
    """`uv run aurora` - sobe a API em http://localhost:8000."""
    uvicorn.run(
        "aurora.api.app:app",
        host=os.getenv("AURORA_HOST", "127.0.0.1"),
        port=int(os.getenv("AURORA_PORT", "8000")),
        log_level=os.getenv("AURORA_LOG_LEVEL", "info"),
    )


async def _restaurar() -> None:
    await storage.restaurar(dados.reservas_iniciais(), dados.visitantes_iniciais())


def restaurar_dados() -> None:
    """`uv run restaurar-dados` - volta reservas e visitantes ao estado inicial.

    As sessoes nao sao apagadas: elas vivem em outro banco (`sessoes.db`) e o
    historico de conversa nao faz parte do estado do condominio.
    """
    asyncio.run(_restaurar())
    print(f"Dados restaurados a partir de dados/ em {caminho_banco_condominio()}.")
    print("As sessoes e os eventos foram preservados.")
