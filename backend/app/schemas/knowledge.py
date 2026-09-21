from pydantic import BaseModel, ConfigDict, Field, field_validator


NO_ANSWER_TEXT = "根据当前已上传的课程资料，无法确认。"
QUESTION_MAX_LENGTH = 2000


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX_LENGTH)

    model_config = ConfigDict(extra="forbid")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class KnowledgeCitation(BaseModel):
    document_id: int
    filename: str
    page_number: int
    chunk_id: int

    model_config = ConfigDict(extra="forbid")


class KnowledgeQueryResponse(BaseModel):
    answerable: bool
    answer: str
    citations: list[KnowledgeCitation]

    model_config = ConfigDict(extra="forbid")


def no_answer_response() -> KnowledgeQueryResponse:
    return KnowledgeQueryResponse(
        answerable=False,
        answer=NO_ANSWER_TEXT,
        citations=[],
    )
