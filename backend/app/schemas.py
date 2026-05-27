from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str


class ModelList(BaseModel):
    data: list[ModelInfo]
