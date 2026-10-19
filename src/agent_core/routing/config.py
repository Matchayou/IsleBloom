"""Operator-frozen model catalog and RouteLLM policy."""
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import ModelCandidate
from .service import Router
from ..adapters.routellm import RouteLLMClient
from ..methods.defaults import published_experts


class CatalogFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(min_length=1)
    strong_model: str | None = None
    weak_model: str | None = None
    checkpoint_path: str | None = None
    threshold: float | None = Field(default=None, ge=0, le=1)
    models: tuple[ModelCandidate, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode='after')
    def pair_matches_catalog(self):
        by_id = {model.id: model for model in self.models}
        if len(by_id) != len(self.models):
            raise ValueError('Model IDs must be unique')
        if len(self.models) == 1:
            if any(value is not None for value in (self.strong_model, self.weak_model,
                                                   self.checkpoint_path, self.threshold)):
                raise ValueError('Single-model catalog cannot configure RouteLLM')
        elif (set(by_id) != {self.strong_model, self.weak_model} or
              self.checkpoint_path is None or self.threshold is None):
            raise ValueError('RouteLLM pair must match the two catalog models and set checkpoint and threshold')
        elif by_id[self.strong_model].capability_rank <= by_id[self.weak_model].capability_rank:
            raise ValueError('Strong model must have higher capability rank')
        return self


def load_router(path):
    return router_from_catalog(CatalogFile.model_validate(json.loads(Path(path).read_text())))


def router_from_catalog(catalog):
    client = (RouteLLMClient(strong_model=catalog.strong_model,
        weak_model=catalog.weak_model, checkpoint_path=catalog.checkpoint_path,
        threshold=catalog.threshold) if len(catalog.models) == 2 else None)
    return Router(published_experts(), catalog.models, client,
        catalog_version=catalog.version)
