from typing import Optional

import voyageai

from config import config

_client: Optional[voyageai.AsyncClient] = None


def _get_client() -> voyageai.AsyncClient:
    global _client
    if _client is None:
        if not config.VOYAGE_API_KEY:
            raise RuntimeError("VOYAGE_API_KEY is not set")
        _client = voyageai.AsyncClient(api_key=config.VOYAGE_API_KEY)
    return _client


async def embed_query(text: str) -> list[float]:
    client = _get_client()
    result = await client.embed([text], model=config.VOYAGE_MODEL, input_type="query")
    return result.embeddings[0]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    result = await client.embed(texts, model=config.VOYAGE_MODEL, input_type="document")
    return result.embeddings
