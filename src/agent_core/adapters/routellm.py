"""RouteLLM's local two-model classifier, separate from answer execution."""
import asyncio
import os
from pathlib import Path
from threading import Lock


class RouteLLMClient:
    def __init__(self, *, strong_model, weak_model, checkpoint_path, threshold):
        if strong_model == weak_model:
            raise ValueError('RouteLLM needs distinct strong and weak models')
        checkpoint = Path(checkpoint_path)
        if not all((checkpoint / name).is_file() for name in
                   ('config.json', 'model.safetensors', 'tokenizer.json')):
            raise ValueError('RouteLLM BERT checkpoint is incomplete')
        if not 0 <= threshold <= 1:
            raise ValueError('RouteLLM threshold must be between 0 and 1')
        self.strong_model = strong_model
        self.weak_model = weak_model
        self.checkpoint_path = str(Path(checkpoint_path).resolve())
        self.threshold = threshold
        self._controller = None
        self._load_lock = Lock()

    def _route(self, prompt):
        if not os.environ.get('OPENAI_API_KEY'):
            raise RuntimeError('OPENAI_API_KEY is required by the RouteLLM package import')
        if self._controller is None:
            with self._load_lock:
                if self._controller is None:
                    # RouteLLM imports LiteLLM, whose default cost-map refresh is unnecessary
                    # because this adapter only asks for a model ID.
                    os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
                    from routellm.controller import Controller
                    self._controller = Controller(routers=['bert'], strong_model=self.strong_model,
                        weak_model=self.weak_model,
                        config={'bert': {'checkpoint_path': self.checkpoint_path}})
        return self._controller.route(prompt, router='bert', threshold=self.threshold)

    async def choose(self, prompt):
        return await asyncio.to_thread(self._route, prompt)
