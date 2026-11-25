"""A pure conversation graph. Even a resumed approval has no executor access."""
import logging
import re
from typing import Callable, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from python_controller.llm.agent_dialogue import ROLE_REPLY, identity_reply
from python_controller.llm.menu_help import explain_menu
from python_controller.llm.agent_intent import requested_plan
from python_controller.llm.agent_semantics import semantic_validate
from python_controller.knowledge.answer import answer_documents, document_question
from python_controller.knowledge.variants import hardware_scope

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    message: str
    history: list
    previous: dict | None
    intent: dict | None
    card: dict | None
    decision: str | None
    error: str | None
    preview: bool
    frozen: dict | None
    edit: dict | None
    citations: list
    reply: str | None
    retain_in_dialogue: bool


class DialogueContext(TypedDict):
    on_partial: Callable[[str], bool] | None
    reference: bool


class ConversationGraph:
    def __init__(self, interpreter, knowledge, responder):
        self.interpreter = interpreter
        self.responder = responder
        self.last_document_question = None
        def classify(state, runtime: Runtime[DialogueContext]):
            previous = state['previous']
            text = state['message']
            if state['frozen']:
                return {'card': state['frozen'], 'intent': None, 'decision': None, 'edit': None, 'error': None}
            followup = bool(self.last_document_question and re.fullmatch(r'(?:그럼\s*)?(?:그건|그 부분|차이는|동작 방식은|더 자세히|좀 더 자세히|왜 그런지)(?:\s*(?:설명해\s*줘|알려\s*줘))?[?？.! ]*',text))
            if not state['preview'] and (followup or document_question(text, knowledge) or hardware_scope(text) is not None):
                return {'intent': {'intent': 'ask_docs'}, 'card': previous, 'decision': None, 'edit': None, 'error': None}
            try:
                if state['preview']:
                    plan = requested_plan(text)
                    change = {'kind': 'append'}
                else:
                    reference = runtime.context['reference']
                    context = {**previous, 'single_move_reference': True} if reference and previous else previous
                    intent = interpreter.interpret(text, history=[], plan=context)
                    verified = semantic_validate(text, intent, previous, reference=reference)
                    return {'intent': None, 'card': previous, 'decision': None, 'edit': None, 'error': None, **verified}
            except ValueError as exc:
                code = str(exc)
                return {'intent': None, 'card': previous, 'decision': None, 'edit': None,
                        'error': code if code.isascii() and code.replace('_', '').isalnum() and code.isupper()
                        else 'INVALID_AGENT_RESPONSE'}
            return {'intent': None, 'decision': None, 'error': None, 'card': previous,
                    'edit': {**change, 'actions': [action.model_dump(mode='json') for action in plan.actions]}}

        def review(state):
            card = state['card']
            response = interrupt({'plan_id': card['id'], 'revision': card['revision']})
            decision = 'invalid'
            if (isinstance(response, dict) and response.get('plan_id') == card['id'] and
                response.get('revision') == card['revision'] and response.get('decision') in ('approve', 'reject', 'continue')):
                decision = response['decision']
            return {'decision': decision}

        def docs(state):
            question = state['message']
            if self.last_document_question and re.fullmatch(r'(?:그럼\s*)?(?:그건|그 부분|차이는|동작 방식은|더 자세히|좀 더 자세히|왜 그런지)(?:\s*(?:설명해\s*줘|알려\s*줘))?[?？.! ]*',question):
                question = self.last_document_question + ' ' + question
            result = answer_documents(knowledge, question)
            self.last_document_question = question[:350]
            return {'intent': None, 'reply': result['reply'], 'citations': result['citations']}

        def dialogue(state, runtime: Runtime[DialogueContext]):
            kind = state['intent']['intent']
            if kind == 'explain_menu':
                return {'intent': None, 'reply': explain_menu(state['intent']['menu']), 'retain_in_dialogue': True}
            if kind == 'identify':
                return {'intent': None, 'reply': identity_reply()}
            if kind == 'role_boundary':
                return {'intent': None, 'reply': ROLE_REPLY}
            try:
                on_partial = runtime.context['on_partial']
                if on_partial and hasattr(responder, 'respond_stream'):
                    reply = responder.respond_stream(state['message'], history=state['history'], kind=kind, on_partial=on_partial)
                else:
                    reply = responder.respond(state['message'], history=state['history'], kind=kind)
            except ValueError as exc:
                code = str(exc)
                if not (code.startswith('NVIDIA_') and code.replace('_', '').isalnum() and code.isupper()):
                    code = 'INVALID_DIALOGUE_RESPONSE'
                return {'intent': None, 'reply': f'대화 답변을 생성하지 못했습니다. ({code})'}
            return {'intent': None, 'reply': reply, 'retain_in_dialogue': True}

        def route(state):
            kind = (state['intent'] or {}).get('intent')
            logger.debug('Agent route=%s', kind or ('edit' if state['edit'] else 'review_or_end'))
            if kind in ('ask_docs','explain_program'):
                return 'docs'
            if kind in ('chat', 'clarify', 'identify', 'role_boundary', 'explain_menu'):
                return 'dialogue'
            return 'review' if state['frozen'] and not state['frozen']['rotation'] else END

        builder = StateGraph(AgentState, context_schema=DialogueContext)
        builder.add_node('classify', classify)
        builder.add_node('review', review)
        builder.add_node('docs', docs)
        builder.add_node('dialogue', dialogue)
        builder.add_edge(START, 'classify')
        builder.add_conditional_edges('classify', route, {'review': 'review', 'docs': 'docs', 'dialogue': 'dialogue', END: END})
        builder.add_edge('review', END)
        builder.add_edge('docs', END)
        builder.add_edge('dialogue', END)
        self.graph = builder.compile(checkpointer=InMemorySaver())
        self.config = {'configurable': {'thread_id': uuid4().hex}}

    def turn(self, message, history, previous, *, preview=False, frozen=None, on_partial=None, reference=False):
        with tracing_context(enabled=False):
            snapshot = self.graph.get_state(self.config)
            if snapshot.next == ('review',):
                self.review(snapshot.values['card'], 'continue')
            return self.graph.invoke({'message': message, 'history': history, 'previous': previous,
                                      'preview': preview, 'frozen': frozen, 'citations': [], 'reply': None,
                                      'retain_in_dialogue': False}, self.config,
                                     context={'on_partial': on_partial, 'reference': reference})

    def review(self, card, decision):
        with tracing_context(enabled=False):
            if self.graph.get_state(self.config).next != ('review',):
                return 'invalid'
            result = self.graph.invoke(Command(resume={'plan_id': card['id'], 'revision': card['revision'],
                                                        'decision': decision}), self.config)
            return result.get('decision')
