"""Rank permitted tools initially and disclose more tools during execution."""
from pydantic import Field
from .contracts import Value, ToolBinding, ToolResult
from .context.retrieval import rank


class SearchTools(Value):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)


DISCOVERY = {'search_tools', 'select_task_tools', 'get_task_status', 'read_context_page',
    'search_skills', 'load_skill', 'read_skill_resource', 'search_memory'}


class ToolRouter:
    def __init__(self, profiles, tools=(), query=None):
        self.profiles = profiles
        self.profile = None
        self.tools = {t.name: t for t in tools}
        self.dynamic = query is not None
        self.selected = set()
        if query is not None:
            self.selected.update(r['name'] for r in self.search(query))

    def search(self, query, limit=5):
        tools = [t for t in self.tools.values() if t.name not in DISCOVERY]
        texts = [t.name + ' ' + t.description + ' ' + ' '.join(p.instructions for p in self.profiles.values()
            if t.name in p.tools) for t in tools]
        return [{'name': tools[i].name, 'description': tools[i].description, 'score': score}
            for i, score in rank(query, texts, limit=limit)]

    def search_binding(self):
        async def search(args):
            found = self.search(args.query, args.limit)
            return ToolResult(value={'tools': found, 'loaded_tools': [t['name'] for t in found],
                'instruction': 'These permitted tools are now available on the next model call. No match only means no lexical match; refine the query or choose a tool group with select_task_tools when available.'})
        return ToolBinding('search_tools', '按当前任务检索并加载可用工具。Search tool names and descriptions; matching tool schemas load on the next model call.', SearchTools, search)

    def restore(self, profile):
        if profile is not None and profile not in self.profiles:
            raise ValueError('Unknown saved tool profile')
        self.profile = profile

    def observe(self, name, result):
        if name == 'load_skill' and 'skill' in result.value:
            self.selected.update(t for t in result.value['skill']['allowed_tools'] if t in self.tools)
        if name == 'search_tools':
            names = result.value['loaded_tools']
            if not set(names) <= self.tools.keys(): raise ValueError('Retrieved tool is not permitted')
            self.selected.update(names)
        if name == 'select_task_tools' and 'tool_profile' in result.value:
            self.restore(result.value['tool_profile'])

    def enabled(self, name):
        if not self.profiles and not self.dynamic: return True
        return name in DISCOVERY or name in self.selected or (
            self.profile is not None and name in self.profiles[self.profile].tools)

    def instructions(self):
        selected = '\n' + self.profiles[self.profile].instructions if self.profile else ''
        return selected + ('\n按需用 search_tools 检索并加载更多获准工具；用 search_skills、load_skill 和 read_skill_resource 渐进加载技能。'
            if self.dynamic else '')
