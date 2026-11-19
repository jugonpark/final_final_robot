"""A pure conversation graph. Even a resumed approval has no executor access."""
from decimal import Decimal
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from pydantic import TypeAdapter

from python_controller.llm.agent_intent import CreatePlan, ModifyPlan, new_plan, revise_plan
from python_controller.planning.schema import Action
from python_controller.planning.validator import ValidatedPlan

ACTION_ADAPTER = TypeAdapter(Action)


def load_plan(actions):
    def load_action(row):
        return ACTION_ADAPTER.validate_python({key: Decimal(value) if key in ('distance_cm', 'angle_deg') else value
                                               for key, value in row.items()})
    return ValidatedPlan(tuple(load_action(row) for row in actions))


class AgentState(TypedDict, total=False):
    message: str
    history: list
    previous: dict | None
    intent: dict | None
    card: dict | None
    decision: str | None


class ConversationGraph:
    def __init__(self, interpreter):
        def classify(state):
            previous = state['previous']
            intent = interpreter.interpret(state['message'], history=state['history'],
                                           plan=previous['actions'] if previous else None)
            if not isinstance(intent, (CreatePlan, ModifyPlan)):
                return {'intent': intent.model_dump(mode='json'), 'card': previous, 'decision': None}
            if isinstance(intent, CreatePlan):
                plan = new_plan(state['message'], intent)
                plan_id, revision = uuid4().hex, 1
            else:
                plan = revise_plan(state['message'], intent, load_plan(previous['actions']) if previous else None)
                plan_id, revision = previous['id'], previous['revision'] + 1
            return {'intent': None, 'decision': None, 'card': {
                'id': plan_id, 'revision': revision, 'rotation': plan.requires_angle_control,
                'actions': [action.model_dump(mode='json') for action in plan.actions],
            }}

        def review(state):
            card = state['card']
            response = interrupt({'plan_id': card['id'], 'revision': card['revision']})
            decision = 'invalid'
            if (isinstance(response, dict) and response.get('plan_id') == card['id'] and
                response.get('revision') == card['revision'] and response.get('decision') in ('approve', 'reject', 'continue')):
                decision = response['decision']
            return {'decision': decision}

        builder = StateGraph(AgentState)
        builder.add_node('classify', classify)
        builder.add_node('review', review)
        builder.add_edge(START, 'classify')
        builder.add_conditional_edges('classify', lambda s: 'review' if s['card'] and not s['card']['rotation'] else END,
                                      {'review': 'review', END: END})
        builder.add_edge('review', END)
        self.graph = builder.compile(checkpointer=InMemorySaver())
        self.config = {'configurable': {'thread_id': uuid4().hex}}

    def turn(self, message, history, previous):
        with tracing_context(enabled=False):
            snapshot = self.graph.get_state(self.config)
            if snapshot.next == ('review',):
                self.review(snapshot.values['card'], 'continue')
            return self.graph.invoke({'message': message, 'history': history, 'previous': previous}, self.config)

    def review(self, card, decision):
        with tracing_context(enabled=False):
            if self.graph.get_state(self.config).next != ('review',):
                return 'invalid'
            result = self.graph.invoke(Command(resume={'plan_id': card['id'], 'revision': card['revision'],
                                                        'decision': decision}), self.config)
            return result.get('decision')
