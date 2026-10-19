"""The model may choose a capability, never a command or a host checkout."""
from typing import Literal
from ..contracts import Value, ToolBinding, ToolResult

class Empty(Value): pass
class ReadCode(Value):
    path: str
    version: Literal['working', 'baseline'] = 'working'
class WriteCode(Value):
    path: str
    text: str
class TestCode(Value):
    profile: str

def bindings(workspace):
    async def read(args): return ToolResult(value={'path': args.path, 'text': workspace.read(args.path, args.version)})
    async def write(args): return ToolResult(value=workspace.write(args.path, args.text))
    async def status(args): return ToolResult(value=workspace.status())
    async def diff(args): return ToolResult(value=workspace.diff())
    async def test(args): return ToolResult(value=await workspace.test(args.profile))
    async def checkpoint(args): return ToolResult(value=workspace.checkpoint())
    async def freeze(args):
        candidate = workspace.freeze()
        return ToolResult(value={'candidate_ref': workspace.key + ':' + candidate.commit,
                                 'candidate': candidate.model_dump(mode='json')})
    return tuple(ToolBinding(name, description, schema, handler) for name, description, schema, handler in (
        ('read_code', 'Read an explicitly granted file at working or fixed baseline version.', ReadCode, read),
        ('write_code', 'Write an explicitly granted file in this task only; invalidates tests.', WriteCode, write),
        ('code_status', 'List granted paths, content hashes and approved test profile names.', Empty, status),
        ('code_diff', 'Review changes against this task fixed baseline, including new files.', Empty, diff),
        ('test_code', 'Run an operator approved trusted test profile on the current code snapshot.', TestCode, test),
        ('checkpoint_code', 'Save admitted changes as a checkpoint commit; does not publish.', Empty, checkpoint),
        ('freeze_code', 'Stop writes and freeze the candidate. Requires all tests on this content.', Empty, freeze)))
