from datetime import date
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.course import CourseRead
from app.schemas.task import TaskRead


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ExtractionRequest(BaseModel):
    text: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class TaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: str | None = Field(max_length=4000)
    deadline: date | None
    priority: Priority

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class ExtractionResult(BaseModel):
    course_name: str | None
    title: str
    deadline: date | None
    priority: Priority
    tasks: list[TaskDraft]

    model_config = ConfigDict(extra="forbid")


class ConfirmedDraft(ExtractionResult):
    title: str = Field(min_length=1, max_length=180)

    @field_validator("title")
    @classmethod
    def confirmed_title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class ExistingCourseSelection(BaseModel):
    mode: Literal["EXISTING"]
    course_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class NewCourseSelection(BaseModel):
    mode: Literal["CREATE_NEW"]
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    teacher: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=20)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("course name must not be blank")
        return value


CourseSelection = Annotated[
    ExistingCourseSelection | NewCourseSelection,
    Field(discriminator="mode"),
]


class ImportRequest(BaseModel):
    course: CourseSelection
    draft: ConfirmedDraft

    model_config = ConfigDict(extra="forbid")


class ImportResult(BaseModel):
    course_created: bool
    course: CourseRead
    task: TaskRead
