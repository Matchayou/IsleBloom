"""SDK public input hook; no conversation reset or tool-call pair removal."""
from copy import deepcopy
import json
from time import time
from agents.run import ModelInputData
from agents import ModelSettings
from ..context.projection import dumps, sha, ContextBudgetExceeded

RAW_WINDOW_TOKENS = 200000
WINDOW_TOKENS = 180000
OUTPUT_TOKENS = 16384
MARGIN_TOKENS = 4096
TOOL_TRIM_THRESHOLD = .6
COMPACTION_THRESHOLD = .7
RECENT_TOOL_RESULTS = 3
IDLE_COMPACTION_SECONDS = 300


def model_settings(timeout=None, *, model=None, purpose='task', finalizing=False):
    from openai.types.shared import Reasoning
    # These are the two documented DeepSeek Responses models. Preserve the SDK
    # protocol and explicit thinking control rather than parsing DSML as tools.
    reasoning = (Reasoning(effort='none' if purpose == 'summary' or finalizing else 'low')
        if model in ('deepseek-flash', 'deepseek-v4-pro') else None)
    output_tokens = 2048 if purpose == 'memory' else 8192 if purpose == 'summary' else OUTPUT_TOKENS
    return ModelSettings(parallel_tool_calls=False, max_tokens=output_tokens,
        timeout=timeout, reasoning=reasoning)


def estimated_tokens(value):
    # Conservative upper bound for text byte-pair encodings. Reported separately
    # from provider-measured usage; never label chars/4 as measured tokens.
    return len(dumps(value).encode())


class ModelProjection:
    def __init__(self, artifacts, model_name=None, *, protected_assistant_contents=()):
        self.artifacts, self.model_name = artifacts, model_name
        self.protected_assistant_contents = protected_assistant_contents
        self.exclusions = []
        self.settings = model_settings()
        self.window_tokens = min(WINDOW_TOKENS, RAW_WINDOW_TOKENS)
        self.force_compaction = False
        self.input_observation = None
        workspace = artifacts.workspace
        row = workspace.db.execute('SELECT digest FROM notes WHERE name=?', ('context_activity',)).fetchone()
        activity = json.loads(workspace.read('context_activity')['text']) if row else None
        self.micro_compaction = bool(activity and time() - activity['last_active_at'] >= IDLE_COMPACTION_SECONDS)

    def __call__(self, data):
        items = deepcopy(data.model_data.input)
        instructions = data.model_data.instructions
        overhead = {'model':self.model_name, 'settings':self.settings.to_json_dict(), 'instructions':instructions,'tools':[{'name':t.name,'description':t.description,
            'parameters':t.params_json_schema} for t in data.agent.tools],
            'output_schema':data.agent.output_type.json_schema() if data.agent.output_type else None}
        self.exclusions = []
        from .input_budget import estimate_input
        def size():
            return estimate_input({**overhead, 'input': items}, None if self.force_compaction else self.input_observation)['tokens'] + OUTPUT_TOKENS + MARGIN_TOKENS
        # The frozen business packet can contain several upstream reports. Keep
        # its required instructions verbatim and page optional data under the
        # actual request budget, using the existing immutable artifact store.
        content = items[0].get('content') if items and items[0].get('role') == 'user' else None
        text = content if isinstance(content,str) else (content[0].get('text')
            if isinstance(content,list) and len(content)==1 and content[0].get('type')=='input_text' else None)
        if self.input_observation and self.input_observation['public_input']['model']==self.model_name and isinstance(text,str):
            previous = self.input_observation['public_input']['input']
            if previous and previous[0].get('role')=='user':
                prior_content = previous[0].get('content')
                prior_text = prior_content if isinstance(prior_content,str) else (prior_content[0].get('text')
                    if isinstance(prior_content,list) and len(prior_content)==1 and prior_content[0].get('type')=='input_text' else None)
                try:
                    original, selected = json.loads(text),json.loads(prior_text) if prior_text else None
                except ValueError: original,selected = None,None
                if isinstance(original,dict) and isinstance(selected,dict) and selected.get('external_context',{}).get('artifact_id')==sha(dumps(original)):
                    items[0]['content'] = prior_content
                    content,text = prior_content,prior_text
        trim_tools = self.micro_compaction or size() >= self.window_tokens * TOOL_TRIM_THRESHOLD
        output_indices = [i for i, item in enumerate(items) if item.get('type') == 'function_call_output']
        fresh_outputs = set(output_indices[-RECENT_TOOL_RESULTS:])
        seen = set()
        calls = {i.get('call_id'): (i.get('name'), i.get('arguments')) for i in items if i.get('type') == 'function_call'}
        for i, item in enumerate(items):
            typ = item.get('type')
            if (item.get('role') == 'assistant' and isinstance(item.get('content'), str)
                    and item['content'] in self.protected_assistant_contents):
                continue
            candidate = typ == 'function_call_output' or item.get('role') == 'assistant'
            if not candidate or i in fresh_outputs: continue
            fingerprint = sha(dumps([calls.get(item.get('call_id')),item.get('output',item.get('content'))]))
            duplicate = typ == 'function_call_output' and fingerprint in seen
            seen.add(fingerprint)
            pressure = size() > self.window_tokens
            if typ == 'function_call_output':
                if not trim_tools and not duplicate and not pressure: continue
                if not self.micro_compaction and not duplicate and size() < self.window_tokens * TOOL_TRIM_THRESHOLD: continue
            elif not duplicate and not pressure:
                continue
            original = dumps(item)
            body = None
            if typ == 'function_call_output':
                try: body = json.loads(item['output']).get('value')
                except (ValueError, TypeError, AttributeError): pass
            if isinstance(body, dict) and body.get('page_type') == 'context':
                # Keep the underlying source reference, never archive a preview
                # wrapper and cause an endless chain of previews of previews.
                key = body['artifact_id']
                reference = dumps({'external_context':{'artifact_id':key,'pointer':body.get('pointer',''),
                    'offset':body['start'],'limit':max(1,body['end']-body['start'])},
                    'next_cursor':body.get('next_cursor'),
                    'reason':'exact_duplicate' if duplicate else 'request_budget'})
            else:
                stored = dumps(body) if body is not None else original
                key = self.artifacts.save_text(stored)
                reference = dumps({'external_context':{'artifact_id':key,'pointer':'','chars':len(stored)},
                    'reason':'exact_duplicate' if duplicate else 'request_budget',
                    'instruction':'Read the fixed original with read_context_page if needed.'})
            if len(reference) >= len(original): continue
            if typ == 'function_call_output': item['output'] = reference
            else: item['content'] = [{'type':'output_text','text':reference}]
            self.exclusions.append({'index':i,'artifact_id':key,'reason':'exact_duplicate' if duplicate else 'request_budget'})
        for fresh_index in sorted(fresh_outputs):
            if size() <= self.window_tokens: break
            # The next model turn must see newly obtained content, even if it is
            # an exact repeat. Evict older optional material first, then deliver
            # a smaller real page with a forward cursor, never a bare same-ID ref.
            item = items[fresh_index]
            try:
                envelope = json.loads(item['output'])
                body = envelope.get('value', envelope)
            except (ValueError, TypeError, AttributeError):
                body = {'text': item['output']}
            from ..context.projection import page
            if not isinstance(body, dict) or body.get('page_type') not in ('context', 'source'):
                text = dumps(body)
                key = self.artifacts.save_text(text)
                body = page(text, key, limit=min(12000, len(text)))
            original_text = body['text']
            original_cursor = body.get('next_cursor')
            def narrowed(count):
                value = {**body, 'text': original_text[:count], 'end': body['start'] + count}
                value['remaining_chars'] = body['total_chars'] - value['end']
                value['complete'] = value['remaining_chars'] == 0
                cursor = ({'artifact_id': body['artifact_id'], 'pointer': body.get('pointer', '')}
                    if body['page_type'] == 'context' else
                    {'ref': body['ref'], 'location': body.get('location', (original_cursor or {}).get('location'))})
                value['next_cursor'] = None if value['complete'] else {
                    **cursor, 'offset': value['end'], 'limit': count}
                return dumps({'value': value})
            minimum = min(128, len(original_text))
            low, high, best = minimum, len(original_text), None
            while low <= high:
                count = (low + high) // 2
                item['output'] = narrowed(count)
                if size() <= self.window_tokens:
                    best = item['output']; low = count + 1
                else: high = count - 1
            item['output'] = best or narrowed(minimum)
            self.exclusions.append({'index': fresh_index, 'reason': 'latest_page_narrowed',
                'start': body['start'], 'end': json.loads(item['output'])['value']['end']})
        if size() > self.window_tokens and isinstance(text,str):
            try: packet = json.loads(text)
            except ValueError: packet = None
            if isinstance(packet,dict) and 'goal' in packet:
                from ..context.projection import project_payload
                empty = deepcopy(items)
                empty[0]['content'] = ''
                required = estimated_tokens({**overhead,'input':empty}) + OUTPUT_TOKENS + MARGIN_TOKENS
                maximum = max(0,self.window_tokens-required)
                while True:
                    prepared = project_payload(packet,maximum)
                    items[0]['content'] = prepared.messages[0].content if isinstance(content,str) else [
                        {**content[0],'text':prepared.messages[0].content}]
                    excess = size()-self.window_tokens
                    if excess <= 0: break
                    # JSON embedded in an SDK text item also pays for escaping.
                    # Check the complete request, not just the packet bytes.
                    maximum = max(0,maximum-excess)
                for key,text in prepared.external_context.items():
                    if self.artifacts.save_text(text) != key: raise ValueError('Context hash mismatch')
                self.exclusions.extend(prepared.projection_exclusions)
        if size() > self.window_tokens:
            raise ContextBudgetExceeded('Required model input, tools and output reserve exceed the token budget')
        self.context_pressure = size() >= self.window_tokens * COMPACTION_THRESHOLD
        return ModelInputData(input=items,instructions=instructions)
