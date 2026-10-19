"""SDK output validation that retains an invalid draft for a bounded correction turn."""
import json
import re
from agents import AgentOutputSchema
from agents.exceptions import ModelBehaviorError
from pydantic import TypeAdapter, ValidationError

from ..contracts import OutputRepair


def _draft_value(raw_output):
    fenced = re.fullmatch(r'\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*', raw_output, re.I)
    draft = fenced.group(1) if fenced else raw_output
    try:
        return json.loads(draft)
    except json.JSONDecodeError:
        return None


def file_contents(raw_output):
    value = _draft_value(raw_output)
    if not isinstance(value, dict) or not isinstance(value.get('files'), list): return ()
    from ..delivery_files import GeneratedFile
    contents = []
    for file in value['files']:
        try: contents.append(GeneratedFile.model_validate(file).content)
        except ValidationError: continue
    return tuple(contents)


def html_documents(raw_output):
    """HTML deliverables are immutable during a JSON-format-only correction."""
    value = _draft_value(raw_output)
    texts = [value.get('report', '')] if isinstance(value, dict) else [raw_output]
    if isinstance(value, dict) and isinstance(value.get('files'), list):
        texts.extend(file.get('content', '') for file in value['files'] if isinstance(file, dict))
    return tuple(document for text in texts if isinstance(text, str)
                 for document in re.findall(r'<!doctype\s+html\b[^>]*>[\s\S]*?</html\s*>', text, re.I))


class RepairableOutputSchema(AgentOutputSchema):
    def validate_json(self, json_str):
        try:
            return super().validate_json(json_str)
        except ModelBehaviorError:
            if '<｜｜DSML｜｜tool_calls>' in json_str:
                return OutputRepair(raw_output=json_str, issues=({'type':'model_protocol',
                    'loc':[], 'msg':'Provider returned textual tool-call markup instead of the structured final response. Tools remain closed; return the final JSON with supported evidence and explicit gaps.'},))
            # The SDK can redact diagnostics. Validate the application model again
            # for field-level feedback, without logging source text or invalid values.
            try:
                TypeAdapter(self.output_type).validate_json(json_str,
                    strict=True if self.is_strict_json_schema() else None)
            except ValidationError as exc:
                issues = tuple({**issue, 'loc': list(issue['loc'])} for issue in
                    exc.errors(include_input=False, include_context=False, include_url=False))
            else:
                issues = ({'msg': 'Output must match the supplied JSON schema.'},)
            return OutputRepair(raw_output=json_str, issues=issues)
