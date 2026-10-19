"""Version assets contain reusable methods, never task snapshots or outputs."""
import hashlib
import json
from string import Formatter
from typing import Literal
from pydantic import Field, model_validator
from ..contracts import Value, RunLimits
from ..research.models import ResearchPlan, PlanStep
from ..orchestration.models import DispatchPolicy
from ..routing.config import CatalogFile


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class MethodBody(Value):
    kind: Literal['search', 'extract', 'analyze', 'synthesize', 'coding']
    instructions: str = Field(min_length=1)
    examples: tuple[str, ...] = ()
    tools: frozenset[Literal['read_source', 'search_source', 'search_web', 'fetch_web', 'read_code', 'write_code', 'code_status', 'code_diff', 'test_code', 'checkpoint_code', 'freeze_code']] = frozenset({'read_source','search_source','search_web','fetch_web'})

    @property
    def prompt(self):
        return self.instructions + ('\n通用示例：\n' + '\n'.join(self.examples) if self.examples else '')

    @property
    def method_digest(self): return hashlib.sha256(self.prompt.encode()).hexdigest()


class TemplateStep(Value):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    goal: str = Field(min_length=1)
    expert_id: str
    method_version: str
    source_parameters: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    limits: RunLimits = Field(default_factory=RunLimits, exclude_if=lambda value: value == RunLimits())


class WorkflowBody(Value):
    parameters: dict[str, Literal['text', 'resource']]
    steps: tuple[TemplateStep, ...] = Field(min_length=1, max_length=30)
    tools: frozenset[Literal['read_source','search_source']] = frozenset({'read_source','search_source'})
    policy: DispatchPolicy = Field(default_factory=DispatchPolicy)

    @model_validator(mode='after')
    def validate_template(self):
        ResearchPlan(research_id='validation', steps=tuple(PlanStep(node_id=s.key,depends_on=s.depends_on) for s in self.steps))
        for name in self.parameters:
            if not name.isidentifier(): raise ValueError('Simple parameter names required')
        for step in self.steps:
            for _, field, spec, conversion in Formatter().parse(step.goal):
                if field is not None and (self.parameters.get(field) != 'text' or spec or conversion):
                    raise ValueError('Only named text placeholders are permitted')
            if any(self.parameters.get(p) != 'resource' for p in step.source_parameters):
                raise ValueError('Source binding must name a resource parameter')
            if not step.source_parameters and not step.depends_on:
                raise ValueError('Each entry step needs new source parameters')
        return self


class AssetVersion(Value):
    category: Literal['method','workflow','routing']
    asset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    body: dict
    author: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    sanitized: Literal[True]

    @model_validator(mode='after')
    def validate_body(self):
        model = {'method':MethodBody,'workflow':WorkflowBody,'routing':CatalogFile}[self.category]
        parsed = model.model_validate(self.body)
        body = parsed.model_dump(mode='json')
        if 'tools' in body: body['tools'] = sorted(body['tools'])
        for model_body in body.get('models',[]):
            model_body['tools'] = sorted(model_body['tools'])
            model_body['supported_kinds'] = sorted(model_body['supported_kinds'])
        object.__setattr__(self, 'body', body)
        if self.category == 'routing' and self.asset_id != 'model-policy':
            raise ValueError('The model routing policy asset ID is model-policy')
        if self.category == 'routing' and parsed.version != self.version:
            raise ValueError('Routing catalog version must match asset version')
        return self

    @property
    def digest(self): return digest(self.model_dump(mode='json'))
