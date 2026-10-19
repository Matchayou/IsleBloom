"""Explicit entry-point policy: no hidden model planner for ordinary conversations."""
from ..contracts import Message, PreparedInput
from .models import DispatchItem, DispatchPlan, DispatchPolicy, RouteDecision

DIMENSIONS = {'cost': '成本', 'speed': '速度', 'limits': '限制'}


class Router:
    def __init__(self, catalog):
        self.catalog = catalog

    def route(self, text: str, *, mode='direct', policy=None, group_id=None):
        if mode == 'direct':
            return RouteDecision(mode='direct', reason='普通会话使用现有执行入口，无规划调用')
        if mode != 'compare':
            raise ValueError('Unknown routing policy')
        items = []
        for key, label in DIMENSIONS.items():
            agent_id = f'expert_{key}'
            self.catalog.get(agent_id)
            items.append(DispatchItem(key=key, agent_id=agent_id,
                input=PreparedInput(messages=(Message(role='user', content=f'仅分析{label}维度。原问题：{text}'),))))
        values = {'items': tuple(items), 'policy': policy or DispatchPolicy()}
        if group_id is not None:
            values['group_id'] = group_id
        return RouteDecision(mode='compare', reason='显式三专家比较策略：成本、速度、限制', plan=DispatchPlan(**values))
