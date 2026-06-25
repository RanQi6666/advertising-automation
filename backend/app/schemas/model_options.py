from pydantic import BaseModel, Field


class ModelOptionRead(BaseModel):
    id: str
    label: str
    provider: str
    is_default: bool = False


class ModelOptionsRead(BaseModel):
    text: list[ModelOptionRead] = Field(default_factory=list)
    image: list[ModelOptionRead] = Field(default_factory=list)
    defaults: dict[str, str | None] = Field(default_factory=dict)
