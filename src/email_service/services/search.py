import json
import logging
from sqlalchemy import text

from email_service.models.database import Email, get_session
from email_service.services import embeddings

logger = logging.getLogger(__name__)


def hybrid_search(
    query: str, max_results: int = 10, account_id: str | None = None
) -> list[dict]:
    semantic_hits = _semantic_search(query, n=max_results, account_id=account_id)
    fts_hits = _fts_search(query, limit=max_results)
    merged = _merge_results(semantic_hits, fts_hits, max_results)
    return _enrich(merged)


def _semantic_search(
    query: str, n: int = 10, account_id: str | None = None
) -> dict[str, float]:
    where = {"account_id": account_id} if account_id else None
    hits = embeddings.search(query, n_results=n, where=where)

    results = {}
    for hit in hits:
        score = max(0.0, 1.0 - hit["distance"])
        results[hit["id"]] = score

    return results


def _fts_search(query: str, limit: int = 10) -> dict[str, float]:
    session = get_session()
    try:
        rows = session.execute(
            text(
                """
                 SELECT e.id, -fts.rank AS score
                 FROM emails_fts fts
                 JOIN emails e on e.rowid = fts.rowid
                 WHERE emails_fts MATCH :query
                 ORDER BY fts.rank
                 LIMIT :limit
            """
            ),
            {"query": query, "limit": limit},
        ).fetchall()

        if not rows:
            return {}

        max_score = max(row[1] for row in rows)
        if max_score == 0:
            return {}

        return {row[0]: row[1] / max_score for row in rows}
    except Exception as e:
        logger.warning(f"FTS search failed: {e}")
        return {}
    finally:
        session.close()


def _merge_results(
    semantic: dict[str, float], fts: dict[str, float], max_results: int
) -> list[tuple[str, float]]:
    all_ids = set(semantic) | set(fts)

    SEMANTIC_WEIGHT = 0.6
    FTS_WEIGHT = 0.4

    scored = []
    for email_id in all_ids:
        s = semantic.get(email_id, 0.0) * SEMANTIC_WEIGHT
        f = fts.get(email_id, 0.0) * FTS_WEIGHT
        scored.append((email_id, s + f))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


def _enrich(results: list[tuple[str, float]]) -> list[dict]:
    if not results:
        return []

    session = get_session()
    try:
        email_ids = [r[0] for r in results]
        score_map = {r[0]: r[1] for r in results}

        emails = session.query(Email).filter(Email.id.in_(email_ids)).all()

        email_map = {}
        for e in emails:
            email_map[e.id] = {
                "email_id": e.id,
                "from_address": e.from_address,
                "from_name": e.from_name,
                "subject": e.subject,
                "received_at": e.received_at,
                "summary": e.summary,
                "category": e.category,
                "priority": e.priority,
                "score": score_map.get(e.id, 0.0),
                "attachments": json.loads(e.attachments) if e.attachments else [],
            }
        return [email_map[eid] for eid, _ in results if eid in email_map]
    finally:
        session.close()
