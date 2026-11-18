from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Move(StrictModel):
    type: Literal["move"]
    direction: Literal["forward", "backward", "left", "right"]
    distance_cm: Annotated[Decimal, Field(ge=5, le=50, allow_inf_nan=False)]

    @field_validator("distance_cm", mode="before")
    @classmethod
    def require_json_number(cls, value):
        if type(value) is int:
            return Decimal(value)
        if not isinstance(value, Decimal):
            raise ValueError("Distance must be a JSON number")
        return value


class Stop(StrictModel):
    type: Literal["stop"]


Action = Annotated[Move | Stop, Field(discriminator="type")]


class ModelPlan(StrictModel):
    needs_clarification: bool
    explanation: Annotated[str, Field(min_length=1, max_length=350)]
    actions: Annotated[list[Action], Field(max_length=4)]
