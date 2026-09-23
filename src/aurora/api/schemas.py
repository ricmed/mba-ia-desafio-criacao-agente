"""Contratos de entrada e saida da API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CriarSessaoPedido(BaseModel):
    apartamento: str


class CriarSessaoResposta(BaseModel):
    session_id: str


class MensagemPedido(BaseModel):
    texto: str


class ConfirmacaoPedido(BaseModel):
    id: str
    confirmado: bool


class ConfirmacaoPendente(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class TurnoResposta(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendente] = Field(default_factory=list)


class ReservaResposta(BaseModel):
    codigo: str
    area: str
    data: str


class VisitanteResposta(BaseModel):
    nome: str
    data: str
