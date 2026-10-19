"""Public HTTP request hook records the exact body before any provider send."""
from contextvars import ContextVar
import json

before_send = ContextVar('controlled_before_send', default=None)
after_receive = ContextVar('controlled_after_receive', default=None)


async def audit_http_request(request):
    callback = before_send.get()
    if callback is None:
        raise RuntimeError('Controlled provider request lacks an audit admission')
    callback(json.loads(request.content), request.content.decode())


async def audit_http_response(response):
    """Keep provider completeness and cache accounting without duplicating response text."""
    callback=after_receive.get()
    if callback is None:return
    callback({'http_status':response.status_code,
        'request_id':response.headers.get('x-request-id')})
    # Reading an SSE response here buffers it until completion and prevents streaming.
    # The broker records completeness and usage from the terminal Responses event.
    if response.headers.get('content-type','').split(';',1)[0].strip() == 'text/event-stream':
        return
    await response.aread()
    try:body=response.json()
    except ValueError:
        import hashlib
        callback({'status_code':response.status_code,'body_parse_error':'invalid_json',
            'content_type':response.headers.get('content-type'),'body_bytes':len(response.content),
            'body_sha256':hashlib.sha256(response.content).hexdigest()})
        return
    if not isinstance(body,dict):return
    metadata={key:body[key] for key in ('id','status','incomplete_details','usage','finish_reason') if key in body}
    if 'choices' in body:metadata['finish_reasons']=[c.get('finish_reason') for c in body['choices']]
    callback(metadata)
