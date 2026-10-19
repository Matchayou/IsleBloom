"""Transient public-answer previews; completed responses still cross the broker ledger."""
from contextvars import ContextVar
from contextlib import aclosing
from time import monotonic

from agents import ModelResponse
from agents.usage import Usage
from pydantic_core import from_json

# Bound to one chat node. Child experts must not publish into their parent's chat.
reply_preview = ContextVar('reply_preview', default=None)


class IncompleteResponse(RuntimeError):
    """A provider-confirmed incomplete response, distinct from transport failure."""
    def __init__(self, response):
        self.response = response
        super().__init__('Provider returned an incomplete response')


def preview_for(request):
    target = reply_preview.get()
    return target[1] if target and target[0] == request.agent_id else None


async def stream_model(model, kwargs, emit, validate, metadata):
    """Consume the provider's public stream, forwarding only answer text, never reasoning."""
    text = ''
    displayed = ''
    response = None
    checked_at = 0
    emit(text='', phase='thinking')
    async with aclosing(model.stream_response(**kwargs)) as events:
        async for event in events:
            # Recheck while consuming; the SSE endpoint also checks access immediately
            # before every public snapshot. Avoid a database round trip per token.
            if monotonic() - checked_at >= .1 or event.type == 'response.completed':
                validate()
                checked_at = monotonic()
            if event.type == 'response.created':
                metadata['id'] = event.response.id
            elif event.type == 'response.output_text.delta':
                text += event.delta
                if kwargs['output_schema'] is None:
                    report = text
                else:
                    try:
                        partial = from_json(text, allow_partial='trailing-strings')
                    except ValueError:
                        continue
                    report = partial.get('report', '') if isinstance(partial, dict) else ''
                if isinstance(report, str) and report != displayed:
                    displayed = report
                    emit(text=report, phase='writing')
            elif event.type in ('response.completed', 'response.incomplete', 'response.failed'):
                terminal = event.response
                metadata.update(terminal.model_dump(include={'status', 'incomplete_details', 'usage'}))
                if event.type == 'response.incomplete' and terminal.status == 'incomplete':
                    raise IncompleteResponse(terminal)
                if event.type != 'response.completed' or terminal.status != 'completed':
                    raise RuntimeError('Provider stream did not complete')
                if terminal.usage is None:
                    raise RuntimeError('Provider stream omitted usage; operation remains unconfirmed')
                usage = terminal.usage
                response = ModelResponse(output=terminal.output, response_id=terminal.id,
                    usage=Usage(requests=1, input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens, total_tokens=usage.total_tokens,
                        input_tokens_details=usage.input_tokens_details,
                        output_tokens_details=usage.output_tokens_details))
            elif event.type in ('error', 'response.error'):
                raise RuntimeError('Provider stream failed')
    if response is None:
        raise RuntimeError('Provider stream ended without a completed response')
    return response
