"""Conversational variants of explicit, measurable dashboard requests."""
import re
import unicodedata

from python_controller.planning.validator import parse_operator_command, validate_plan, ValidatedPlan


def normalize_dashboard_command(text):
    text = unicodedata.normalize('NFC', text).strip()
    text = re.sub(r'^(?:로봇아|디아이쥐|D\.I\.G\.)[,\s]*', '', text, flags=re.I)
    text = re.sub(r'^(?:먼저|일단)\s+', '', text)
    text = re.sub(r'전진(?:해서|하고)\s*(?=\d)', '앞으로 ', text)
    text = re.sub(r'후진(?:해서|하고)\s*(?=\d)', '뒤로 ', text)
    text = re.sub(r'(?<=cm)만\s*', '만큼 ', text, flags=re.I)
    text = re.sub(r'이동(?:해|시켜)\s*줘', '이동해줘', text)
    text = re.sub(r'움직여\s*줘', '움직여줘', text)
    text = re.sub(r'가\s+줘', '가줘', text)
    text = re.sub(r'(?:가\s*주고|이동해\s*주고|움직여\s*주고)', '가고', text)
    text = re.sub(r'(간|이동한|회전한|돈)\s+(뒤|다음|후)에\s+', r'\1 \2 ', text)
    text = re.sub(r'(가고|이동하고|움직이고|회전하고|돌고)\s+나서\s+', r'\1 ', text)
    text = re.sub(r'\s*(?:부탁해|부탁해요)[.!。！]?$', '', text)
    return text.strip()


def prepare_dashboard_plan(command, config):
    from python_controller.llm.nvidia_client import DEFAULT_BASE_URL, NvidiaPlanner
    from python_controller.planning.schema import Stop
    from python_controller.planning.validator import PlanError, STOP_WORDS

    if not command.strip() or len(command) > 350:
        raise PlanError('INPUT_LENGTH')
    normalized = normalize_dashboard_command(command)
    if normalized.rstrip('.!。！') in STOP_WORDS:
        return ValidatedPlan((Stop(type='stop'),))
    planner = NvidiaPlanner(api_key=config.get('NVIDIA_API_KEY'),
                            base_url=config.get('NVIDIA_BASE_URL', DEFAULT_BASE_URL),
                            model=config.get('NVIDIA_MODEL'))
    requested = parse_operator_command(normalized, allow_rotation_preview=True)
    raw = planner.plan(command)
    plan = validate_plan(raw, requested)
    if plan.needs_clarification:
        raise PlanError('MODEL_REQUESTED_CLARIFICATION')
    return plan
