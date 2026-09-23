"""Configuracao central: caminhos, modelo e variaveis de ambiente."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]

load_dotenv(RAIZ / ".env")

DADOS_DIR = RAIZ / "dados"

APP_NAME = "residencial-aurora"


def _db_dir() -> Path:
    destino = Path(os.getenv("AURORA_DB_DIR", "var"))
    if not destino.is_absolute():
        destino = RAIZ / destino
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def caminho_banco_condominio() -> Path:
    """Banco com reservas, visitantes e confirmacoes (estado mutavel)."""
    return _db_dir() / "condominio.db"


def caminho_banco_sessoes() -> Path:
    """Banco com as sessoes e os eventos do ADK."""
    return _db_dir() / "sessoes.db"


def modelo_padrao() -> str:
    return os.getenv("AURORA_MODELO", "gemini-2.5-flash")
