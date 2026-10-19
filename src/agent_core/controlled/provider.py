"""Lazy audited Responses provider: read-only operator commands need no API key."""
from agents import ModelProvider


class LiveModelProvider(ModelProvider):
    def __init__(self):
        self.provider = None

    def get_model(self, model_name):
        if self.provider is None:
            from agents.models.openai_provider import OpenAIProvider
            from openai import AsyncOpenAI, DefaultAsyncHttpxClient
            from ..context.audit import audit_http_request, audit_http_response
            self.provider = OpenAIProvider(openai_client=AsyncOpenAI(max_retries=0,
                http_client=DefaultAsyncHttpxClient(event_hooks={
                    'request': [audit_http_request], 'response': [audit_http_response]})), use_responses=True)
        return self.provider.get_model(model_name)
