from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CourseBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    teacher: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Course name cannot be blank")
        return value


class CourseCreate(CourseBase):
    pass


class CourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    teacher: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Course name cannot be blank")
        return value.strip() if value is not None else value


class CourseRead(CourseBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

