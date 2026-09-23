"""Consulta ao regulamento interno (Garantia 4).

O regulamento tem catorze capitulos e mais de quarenta mil caracteres. Ele e
indexado **uma vez na memoria do processo** e nunca entra em instrucao de
agente nem em evento de sessao por inteiro: a tool devolve no maximo tres
artigos, todos pertinentes a pergunta. Assim a resposta e fundamentada no
documento, mas o historico da sessao nao passa a carregar capitulos que
tratam de outros assuntos em toda chamada seguinte ao modelo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .dados import texto_regulamento

_MAX_ARTIGOS = 3

# Palavras sem valor discriminante na busca.
_VAZIAS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos", "e",
    "em", "essa", "esse", "esta", "este", "eu", "ha", "isso", "na", "nas", "no",
    "nos", "o", "os", "ou", "para", "pela", "pelo", "por", "posso", "pode",
    "qual", "quais", "quando", "que", "quem", "se", "sobre", "sao", "tem", "um",
    "uma", "e_", "ate", "meu", "minha", "muito", "mais",
}

# Sinonimos que aproximam a fala do morador do vocabulario do regulamento.
_SINONIMOS: dict[str, tuple[str, ...]] = {
    "horario": ("funciona", "funcionamento", "periodo", "uso", "aberta", "fecha"),
    "horarios": ("funciona", "funcionamento", "periodo", "uso"),
    "horas": ("horario", "funciona", "funcionamento", "periodo"),
    "fecha": ("horario", "funcionamento", "periodo"),
    "fechamento": ("horario", "funcionamento", "periodo"),
    "abre": ("horario", "funcionamento", "periodo"),
    "domingo": ("domingos", "feriados"),
    "domingos": ("domingo", "feriados"),
    "sabado": ("sabados",),
    "cachorro": ("animais", "estimacao", "animal"),
    "cachorros": ("animais", "estimacao", "animal"),
    "gato": ("animais", "estimacao", "animal"),
    "pet": ("animais", "estimacao", "animal"),
    "barulho": ("silencio", "ruido", "sonoro", "convivencia"),
    "musica": ("silencio", "ruido", "sonoro"),
    "silencio": ("convivencia", "ruido", "sonoro"),
    "quieto": ("silencio", "convivencia"),
    "obra": ("obras", "reformas", "reforma"),
    "reformar": ("obras", "reformas", "reforma"),
    "reforma": ("obras", "reformas"),
    "autorizacao": ("previa", "aprovacao"),
    "lixo": ("coleta", "residuos", "reciclagem"),
    "carro": ("garagem", "veiculos", "veiculo", "vaga"),
    "vaga": ("garagem", "veiculos", "estacionamento"),
    "visita": ("visitantes", "visitante", "portaria"),
    "visitas": ("visitantes", "visitante", "portaria"),
    "multa": ("infracoes", "penalidades", "advertencia"),
    "festa": ("salao", "festas", "churrasqueira"),
    "salao": ("festas", "salao"),
    "quadra": ("poliesportiva",),
    "academia": ("ginastica", "exercicios"),
    "crianca": ("menores", "criancas", "playground", "brinquedoteca"),
    "criancas": ("menores", "playground", "brinquedoteca"),
    "mudanca": ("mudancas", "elevador"),
    "taxa": ("taxa", "pagamento", "cobranca"),
}


@dataclass(frozen=True)
class Artigo:
    capitulo: str
    identificador: str
    texto: str
    termos: frozenset[str]
    termos_capitulo: frozenset[str]


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento


def _tokenizar(texto: str) -> set[str]:
    brutos = re.findall(r"[a-z0-9]+", _normalizar(texto))
    return {t for t in brutos if len(t) > 2 and t not in _VAZIAS}


def _expandir(termos: set[str]) -> set[str]:
    expandidos = set(termos)
    for termo in termos:
        expandidos.update(_SINONIMOS.get(termo, ()))
    return expandidos


@lru_cache(maxsize=1)
def _indice() -> tuple[Artigo, ...]:
    """Quebra o regulamento em artigos, cada um marcado com seu capitulo."""
    artigos: list[Artigo] = []
    capitulo_atual = "Disposicoes"
    identificador_atual = ""
    buffer: list[str] = []

    def fechar() -> None:
        if identificador_atual and buffer:
            corpo = "\n".join(buffer).strip()
            artigos.append(
                Artigo(
                    capitulo=capitulo_atual,
                    identificador=identificador_atual,
                    texto=corpo,
                    termos=frozenset(_tokenizar(capitulo_atual + " " + corpo)),
                    termos_capitulo=frozenset(_tokenizar(capitulo_atual)),
                )
            )

    for linha in texto_regulamento().splitlines():
        if linha.startswith("## "):
            fechar()
            identificador_atual, buffer = "", []
            capitulo_atual = linha[3:].strip()
            continue

        inicio_artigo = re.match(r"\*\*(Art\.\s*\d+[^*]*)\*\*", linha)
        if inicio_artigo:
            fechar()
            identificador_atual = inicio_artigo.group(1).strip().rstrip(".")
            buffer = [linha]
            continue

        if identificador_atual:
            buffer.append(linha)

    fechar()
    return tuple(artigos)


def buscar(pergunta: str) -> list[dict[str, str]]:
    """Devolve ate tres artigos pertinentes a pergunta.

    A pontuacao e deterministica (sobreposicao de termos, com peso extra para
    o capitulo mais relevante); o modelo nao escolhe o que sai do documento.
    """
    consulta = _expandir(_tokenizar(pergunta))
    if not consulta:
        return []

    pontuados: list[tuple[float, Artigo]] = []
    for artigo in _indice():
        comuns = consulta & artigo.termos
        if not comuns:
            continue
        # Um termo que aparece no titulo do capitulo vale mais: e ele que diz
        # de que assunto o artigo trata.
        peso = sum(3.0 if t in artigo.termos_capitulo else 1.0 for t in comuns)
        # Normaliza pelo tamanho do artigo para nao privilegiar os mais longos.
        pontuados.append((peso / (1 + len(artigo.termos) ** 0.5), artigo))

    if not pontuados:
        return []

    pontuados.sort(key=lambda par: par[0], reverse=True)

    # Prioriza os artigos do capitulo do melhor resultado: manter a resposta
    # dentro de um unico assunto e o que impede trechos de outros capitulos
    # de entrarem no historico da sessao.
    capitulo_alvo = pontuados[0][1].capitulo
    do_capitulo = [par for par in pontuados if par[1].capitulo == capitulo_alvo]
    selecionados = (do_capitulo or pontuados)[:_MAX_ARTIGOS]

    return [
        {
            "capitulo": artigo.capitulo,
            "artigo": artigo.identificador,
            "texto": artigo.texto,
        }
        for _, artigo in selecionados
    ]


def capitulos() -> list[str]:
    vistos: list[str] = []
    for artigo in _indice():
        if artigo.capitulo not in vistos:
            vistos.append(artigo.capitulo)
    return vistos
