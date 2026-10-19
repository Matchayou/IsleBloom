"""SDK public Model interface; provider requests and budgets are controlled by the broker."""
from agents import Model, ModelResponse
from pydantic import TypeAdapter

RESPONSE = TypeAdapter(ModelResponse)


def response_json(response):
    """Keep the required nullable envelope and canonical public output fields.

    SDK state restoration rejects aliased tool fields materialized as null extras.
    Optional output nulls carry no state; the required response_id may be null.
    """
    value = RESPONSE.dump_python(response, mode='json', by_alias=True, exclude_unset=True)
    value['output'] = [item.model_dump(mode='json', by_alias=True, exclude_none=True)
                       for item in response.output]
    return value


class BrokerModel(Model):
    def __init__(self, channel):
        self.channel = channel

    async def get_response(self, system_instructions, input, model_settings, tools,
                           output_schema, handoffs, tracing, **kwargs):
        if handoffs or any(kwargs.get(k) is not None for k in
                           ('previous_response_id', 'conversation_id', 'prompt')):
            raise ValueError('CE1 requires a local stateless SDK conversation')
        value = await self.channel.call('model', {'input': input, 'instructions':system_instructions,
                                      'tools': [t.name for t in tools]})
        return RESPONSE.validate_python(value)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError('CE1 uses non-streaming SDK execution')
        yield
