"""Validate the model response against both the policy and the operator's request."""
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, localcontext

from python_controller.planning.schema import Action, ModelPlan, Move, Rotate, Stop


POLICY_VERSION = "A+B-1.1"
STOP_WORDS = {"stop", "정지", "정지해", "정지해줘", "정지하세요", "멈춰", "멈춰줘", "멈추세요"}
DIRECTIONS = {
    "앞": "forward", "전방": "forward", "전진": "forward",
    "뒤": "backward", "후방": "backward", "후진": "backward",
    "왼쪽": "left", "좌측": "left", "좌": "left",
    "오른쪽": "right", "우측": "right", "우": "right",
}
TURN_DIRECTIONS = {**DIRECTIONS, "반시계 방향": "left", "시계 방향": "right"}
UNITS = {
    "cm": Decimal(1), "센티미터": Decimal(1),
    "mm": Decimal("0.1"), "밀리미터": Decimal("0.1"),
    "m": Decimal(100), "미터": Decimal(100),
}
MOVE_TEXT = re.compile(
    r"(?:로봇(?:\s*몸체)?\s*기준(?:으로)?\s+)?"
    r"(?P<direction>앞|전방|전진|뒤|후방|후진|왼쪽|좌측|오른쪽|우측|좌(?=로)|우(?=로))(?:으로|로)?\s*"
    r"(?P<distance>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>cm|mm|m|센티미터|밀리미터|미터)"
    r"(?:를|만큼)?\s*"
    r"(?:가|가줘|가세요|가주세요|가고|간 뒤|간 다음|간 후|"
    r"이동|이동해|이동해줘|이동하세요|이동해 주세요|이동하고|이동한 뒤|이동한 다음|이동한 후|"
    r"움직여|움직여줘|움직이고|움직여 주세요|하고)?"
)
CONNECTOR = re.compile(
    r"\s*[,，]\s*(?:그리고\s+)?|\s+(?:그리고|그다음|그 다음)\s+|"
    r"(?:(?<=가고)|(?<=이동하고)|(?<=움직이고)|(?<=간 뒤)|(?<=간 다음)|(?<=간 후)|"
    r"(?<=이동한 뒤)|(?<=이동한 다음)|(?<=이동한 후)|(?<=회전하고)|"
    r"(?<=회전한 뒤)|(?<=회전한 다음)|(?<=회전한 후)|(?<=돌고)|(?<=돈 뒤)|(?<=돈 다음)|(?<=돈 후)|(?<=하고))\s+"
)
INCOMPLETE = re.compile(r"(?:가고|하고|움직이고|돌고|(?:간|이동한|회전한|돈) (?:뒤|다음|후))$")
ROTATE_TEXT = re.compile(
    r"(?:(?P<direction>왼쪽|오른쪽|좌측|우측|좌(?=로)|우(?=로)|반시계 방향|시계 방향)(?:으로|로)?\s*)?"
    r"(?:(?P<turns>한|1|반)\s*바퀴|(?P<angle>[0-9]+(?:\.[0-9]+)?)\s*(?:도|°))(?:를|만큼)?\s*"
    r"(?P<verb>회전(?:해(?:줘| 주세요)?|하세요|하고|한 (?:뒤|다음|후))?|돌아(?:줘)?|돌고|돈 (?:뒤|다음|후))?"
)


class PlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    actions: tuple[Action, ...]
    needs_clarification: bool = False

    def __post_init__(self):
        if not isinstance(self.actions, tuple) or not all(isinstance(action, (Move, Rotate, Stop)) for action in self.actions):
            raise PlanError("INVALID_VALIDATED_ACTIONS")
        if self.needs_clarification:
            if self.actions:
                raise PlanError("CLARIFICATION_WITH_ACTIONS")
        else:
            _check_actions(self.actions)

    @property
    def requires_angle_control(self):
        return any(isinstance(action, Rotate) for action in self.actions)


def _check_actions(actions):
    if not 1 <= len(actions) <= 4:
        raise PlanError("ACTION_COUNT")
    if any(isinstance(action, Stop) for action in actions[:-1]):
        raise PlanError("STOP_MUST_BE_LAST")
    distances = [action.distance_cm for action in actions if isinstance(action, Move)]
    with localcontext() as context:
        context.prec = max((len(distance.as_tuple().digits) for distance in distances), default=1) + 4
        if sum(distances) > 100:
            raise PlanError("TOTAL_DISTANCE_LIMIT")


def parse_operator_command(text, *, allow_rotation_preview=False):
    if not text.strip() or len(text) > 350:
        raise PlanError("INPUT_LENGTH")
    normalized = ' '.join(unicodedata.normalize("NFC", text).lower().split())
    normalized = re.sub(r"[.!。！]\Z", "", normalized)
    clauses = CONNECTOR.split(normalized)
    if len(clauses) > 4:
        raise PlanError("ACTION_COUNT")
    if INCOMPLETE.search(clauses[-1]):
        raise PlanError("COMMAND_REQUIRES_CLARIFICATION")
    actions = []
    with localcontext() as context:
        # Unit conversion and limit checks must not round a value into range.
        context.prec = 354
        for clause in clauses:
            if clause in STOP_WORDS:
                actions.append(Stop(type="stop"))
                continue
            rotation = ROTATE_TEXT.fullmatch(clause) if allow_rotation_preview else None
            if rotation and (rotation["verb"] or (rotation["direction"] and rotation["turns"])):
                angle = Decimal(rotation["angle"] or (180 if rotation["turns"] == "반" else 360))
                if not 0 < angle <= 360:
                    raise PlanError("ROTATE_ANGLE_LIMIT")
                actions.append(Rotate(type="rotate", direction=TURN_DIRECTIONS.get(rotation["direction"]), angle_deg=angle))
                continue
            match = MOVE_TEXT.fullmatch(clause)
            if not match:
                raise PlanError("COMMAND_REQUIRES_CLARIFICATION")
            distance = Decimal(match["distance"]) * UNITS[match["unit"]]
            if not 5 <= distance <= 50:
                raise PlanError("MOVE_DISTANCE_LIMIT")
            actions.append(Move(type="move", direction=DIRECTIONS[match["direction"]], distance_cm=distance))
    _check_actions(actions)
    return tuple(actions)


def strict_json_object(raw, *, parse_float=float):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("NONFINITE_JSON_NUMBER")

    data = json.loads(raw, object_pairs_hook=unique_fields, parse_constant=reject_constant, parse_float=parse_float)
    if not isinstance(data, dict):
        raise ValueError("EXPECTED_JSON_OBJECT")
    return data


def validate_plan(raw, requested_actions):
    if not isinstance(raw, str) or len(raw) > 4096:
        raise PlanError("INVALID_RESPONSE_SIZE")
    try:
        plan = ModelPlan.model_validate(strict_json_object(raw, parse_float=Decimal))
    except (ValueError, ArithmeticError, RecursionError):
        raise PlanError("INVALID_PLAN_JSON_OR_SCHEMA") from None
    actions = tuple(plan.actions)
    if plan.needs_clarification:
        return ValidatedPlan(actions, needs_clarification=True)
    validated = ValidatedPlan(actions)
    if actions != requested_actions:
        raise PlanError("PLAN_DOES_NOT_MATCH_COMMAND")
    return validated
