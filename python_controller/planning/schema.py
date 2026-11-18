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


class Rotate(StrictModel):
    type: Literal["rotate"]
    direction: Literal["left", "right"] | None = None
    angle_deg: Annotated[Decimal, Field(gt=0, le=360, allow_inf_nan=False)]

    @field_validator("angle_deg", mode="before")
    @classmethod
    def require_json_number(cls, value):
        return Move.require_json_number(value)


Action = Annotated[Move | Rotate | Stop, Field(discriminator="type")]


def describe_action(action):
    directions = {"forward": "앞으로", "backward": "뒤로", "left": "왼쪽으로", "right": "오른쪽으로"}
    if isinstance(action, Move):
        return f"{directions[action.direction]} {action.distance_cm:g} cm"
    if isinstance(action, Rotate):
        direction = directions.get(action.direction, "방향 미지정")
        return f"{direction} {action.angle_deg:g}° 회전 (각도 제어 검증 대기)"
    return "정지"


class ModelPlan(StrictModel):
    needs_clarification: bool
    explanation: Annotated[str, Field(min_length=1, max_length=350)]
    actions: Annotated[list[Action], Field(max_length=4)]
