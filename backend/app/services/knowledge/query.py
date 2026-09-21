from app.schemas.knowledge import (
    KnowledgeCitation,
    KnowledgeQueryResponse,
    no_answer_response,
)
from app.services.knowledge.retrieval import RetrievedChunk
from app.services.knowledge.schemas import GroundedAnswer


class GroundingValidationError(RuntimeError):
    pass


def build_grounded_response(
    answer: GroundedAnswer,
    candidates: list[RetrievedChunk],
) -> KnowledgeQueryResponse:
    if not answer.answerable:
        return no_answer_response()

    normalized_answer = answer.answer.strip()
    if not normalized_answer:
        raise GroundingValidationError("An answerable result has no answer")
    if not answer.used_chunk_ids:
        raise GroundingValidationError("An answerable result has no cited chunks")

    candidates_by_id = {candidate.chunk_id: candidate for candidate in candidates}
    unknown_ids = [
        chunk_id
        for chunk_id in answer.used_chunk_ids
        if chunk_id not in candidates_by_id
    ]
    if unknown_ids:
        raise GroundingValidationError("The model referenced a non-candidate chunk")

    citations: list[KnowledgeCitation] = []
    seen: set[int] = set()
    for chunk_id in answer.used_chunk_ids:
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        candidate = candidates_by_id[chunk_id]
        if candidate.page_number is None:
            raise GroundingValidationError("A cited chunk has no page number")
        citations.append(
            KnowledgeCitation(
                document_id=candidate.document_id,
                filename=candidate.filename,
                page_number=candidate.page_number,
                chunk_id=candidate.chunk_id,
            )
        )
    if not citations:
        raise GroundingValidationError("An answerable result produced no citations")
    return KnowledgeQueryResponse(
        answerable=True,
        answer=normalized_answer,
        citations=citations,
    )
