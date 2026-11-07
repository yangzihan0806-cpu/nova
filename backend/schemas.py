"""Frozen CONTRACT v1.0 request models (also imported by D's planner)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class JobParams(RequestModel):
    width: StrictInt = Field(ge=64, le=4096)
    height: StrictInt = Field(ge=64, le=4096)
    tile_size: StrictInt
    max_iter: StrictInt = Field(ge=1, le=20000)
    x_min: float = Field(ge=-4, le=4, allow_inf_nan=False)
    x_max: float = Field(ge=-4, le=4, allow_inf_nan=False)
    y_min: float = Field(ge=-4, le=4, allow_inf_nan=False)
    y_max: float = Field(ge=-4, le=4, allow_inf_nan=False)

    @model_validator(mode='after')
    def validate_grid(self):
        if self.tile_size not in (32, 64, 128):
            raise ValueError('tile_size must be 32, 64 or 128')
        if self.width % self.tile_size or self.height % self.tile_size:
            raise ValueError('width and height must be multiples of tile_size')
        if (self.width // self.tile_size) * (self.height // self.tile_size) > 4096:
            raise ValueError('task_count must be at most 4096')
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError('coordinate minima must be less than maxima')
        return self


class JobCreate(RequestModel):
    task_type: Literal['mandelbrot_v1']
    params: JobParams


class RegisterReq(RequestModel):
    name: str = Field(min_length=1, max_length=40)
    worker_type: Literal['browser', 'native']
    slots: StrictInt = Field(default=1, ge=1, le=8)
    has_gpu: bool = False
    task_types: list[str]
    client_info: str | None = Field(default=None, max_length=200)


class CompleteReq(RequestModel):
    worker_id: str
    lease_token: str
    # CONTRACT-NOTE: Keep result values uncoerced and validate in the scheduler
    # so invalid payloads release the lease transactionally before returning 422.
    result: Any = Field(json_schema_extra={'type': 'array', 'items': {'type': 'integer'}})
    # CONTRACT-NOTE: runtime_ms must fit the schema's SQLite INTEGER storage.
    runtime_ms: StrictInt = Field(ge=0, le=2**63 - 1)


class FailReq(RequestModel):
    worker_id: str
    lease_token: str
    error: str = Field(max_length=500)


class EmptyReq(RequestModel):
    pass


class PlannerReq(RequestModel):
    prompt: str = Field(max_length=500)
