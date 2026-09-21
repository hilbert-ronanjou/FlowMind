from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Self


class GroundedAnswer(BaseModel):
    answerable: bool
    answer: str = Field(max_length=8000)
    used_chunk_ids: list[int] = Field(max_length=5)

    model_config = ConfigDict(extra="forbid")

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        return value.strip()

    @field_validator("used_chunk_ids")
    @classmethod
    def chunk_ids_must_be_positive(cls, value: list[int]) -> list[int]:
        if any(chunk_id < 1 for chunk_id in value):
            raise ValueError("used chunk IDs must be positive")
        return value

    @model_validator(mode="after")
    def answerable_result_requires_support(self) -> Self:
        if self.answerable and (not self.answer or not self.used_chunk_ids):
            raise ValueError("an answerable result requires an answer and used chunks")
        return self
