"""Check motion-related semantic intent against the current operator utterance."""

import re

from python_controller.llm.agent_intent import (
    CreatePlan, CreateSequence, DraftCommand, EditPlan, EditPlanProposal, ModeThenPlan, ModifyPlan, Navigate,
    ReorderDraft, StageMotion, SwitchMode, UNITS, apply_proposal, draft_command, edit_plan, explicit_composite,
    explicit_mode, new_plan, reorder_coordinates, revise_plan,
)
from python_controller.mission.draft_store import load_plan
from python_controller.planning.schema import Move


PAGE_NAMES = {
    'ai_chat': r'ai\s*(?:대화|채팅)|미션\s*컨트롤|자연어\s*제어',
    'camera': r'카메라(?:\s*[·ㆍ]\s*손동작)?',
    'manual': r'수동\s*제어|직접\s*제어',
    'diagnostics': r'(?:상태\s*[·ㆍ]\s*)?진단',
    'settings': r'설정',
}
DISTANCE_REFERENCE = re.compile(
    rf'(?:그걸|그거(?:를)?|그\s*동작(?:을)?)\s+([0-9]+(?:\.[0-9]+)?)\s*({"|".join(UNITS)})'
    r'\s*(?:으로|로)\s*(?:바꿔|수정(?:해)?|변경(?:해)?|고쳐)(?:\s*(?:줘|주세요))?[.!?。！？]?', re.I)
VAGUE_REFERENCE = re.compile(
    r'(?:조금|좀|살짝)\s*더(?:\s*(?:해|해\s*줘|움직여))?|'
    r'아까(?:처럼|\s*하던\s*대로)(?:\s*(?:다시|한\s*번\s*더)?\s*(?:해|해\s*줘)?)?|'
    r'그거\s*더\s*해\s*줘', re.I)
REFERENCE_CLARIFY = '어느 미션 블록을 바꾸고 싶은지 단계 번호와 거리 또는 방향을 알려 주세요.'


def distance_reference(text):
    return DISTANCE_REFERENCE.fullmatch(text.strip())


def vague_reference(text):
    return VAGUE_REFERENCE.fullmatch(text.strip().rstrip('.!?。！？'))


def _explicit_page(text, page):
    target = PAGE_NAMES[page]
    return bool(re.fullmatch(rf'(?:{target})\s*(?:화면|창|페이지|탭|ui|뷰|메뉴)?\s*(?:으로|로|을|를)?\s*'
                             r'(?:열어(?:\s*(?:줘|주세요))?|열기|보여(?:\s*(?:줘|주세요|줄래))?|보기|'
                             r'띄워(?:\s*(?:줘|주세요))?|이동(?:해(?:\s*(?:줘|주세요))?)?|'
                             r'넘어가(?:\s*줘)?|가(?:\s*줘)?)', text.strip().rstrip('.!?。！？'), re.I))


def semantic_validate(text, intent, card, *, reference=False):
    """Return only verified proposals. Permission and dispatch stay with the host."""
    if isinstance(intent, (CreatePlan, CreateSequence, StageMotion)):
        plan = new_plan(text, intent)
        return {'edit': {'kind': 'append', 'actions': [action.model_dump(mode='json') for action in plan.actions]}}

    if isinstance(intent, (EditPlanProposal, EditPlan, ModifyPlan)):
        old = load_plan(card['actions']) if card else None
        if isinstance(intent, EditPlanProposal):
            op = intent.operation
            if match := distance_reference(text):
                if not (reference and old and len(old.actions) == 1 and isinstance(old.actions[0], Move)):
                    raise ValueError('REFERENCE_CONTEXT_REQUIRED')
                if op.kind != 'replace_distance' or op.selector.kind != 'ordinal' or op.selector.value != 1:
                    raise ValueError('ACTION_INDEX_MISMATCH')
                # Reuse the explicit edit parser with the one proven referent and the original value and unit.
                text = f'첫 번째 거리를 {match[1]}{match[2]}로 수정해'
            plan = apply_proposal(text, op, old)
            index = (len(old.actions)-1 if op.selector.kind == 'last' else op.selector.value-1) if op.selector else None
            kind = ('insert' if op.kind.startswith('insert_') else
                    'replace' if op.kind.startswith('replace_') else op.kind)
            if op.kind == 'insert_after':
                index += 1
        elif isinstance(intent, EditPlan):
            plan = edit_plan(text, intent, card, old)
            kind = intent.operation.kind
            index = intent.operation.index-1 if intent.operation.index else None
        else:
            plan = revise_plan(text, intent, old)
            kind, index = 'replace', intent.action_index-1
        return {'edit': {'kind': kind, 'index': len(old.actions) if kind == 'append' else index,
                         'actions': [action.model_dump(mode='json') for action in plan.actions]}}

    if isinstance(intent, Navigate) and not _explicit_page(text, intent.page):
        raise ValueError('NAVIGATION_DOES_NOT_MATCH_COMMAND')
    if isinstance(intent, SwitchMode) and not explicit_mode(text, intent.mode):
        raise ValueError('MODE_DOES_NOT_MATCH_COMMAND')
    value = intent.model_dump(mode='json')
    if isinstance(intent, ModeThenPlan):
        value['motion_text'] = explicit_composite(text, intent)
    if isinstance(intent, DraftCommand) and intent.intent != 'show_draft' and draft_command(text) != intent.intent:
        raise ValueError('DRAFT_COMMAND_DOES_NOT_MATCH_COMMAND')
    if isinstance(intent, ReorderDraft) and (not card or reorder_coordinates(text, len(card['blocks'])) !=
                                             (intent.source, intent.destination)):
        raise ValueError('ACTION_INDEX_MISMATCH')
    return {'intent': value}
