"""Initial method assets; execution resolves immutable registry versions."""
import hashlib
from ..coding.models import CODING_TOOLS

METHODS = {
    'coding': ('coding-v4', '完成授权范围内的代码任务，检查改动，运行项目测试并交付冻结的代码候选。'),
    'search': ('search-v3', '查找与问题相关的可靠资料，整理来源和关键信息。'),
    'extract': ('extract-v3', '依据任务的字段要求提取材料，保留对应来源和原始含义。'),
    'analyze': ('analyze-v3', '分析材料与证据，解释关键关系，给出有依据的判断。'),
    'synthesize': ('synthesize-v3', '整合相关材料与专家结果，形成清晰、连贯的结论。'),
}
FINANCIAL_EXTRACT = ('extract-financial-v3', '提取任务要求的财务信息，保留公司、期间、单位、口径及来源。')
EXPERT_METHODS = {f'expert_{kind}': (kind, *method) for kind, method in METHODS.items()}
EXPERT_METHODS['expert_extract_financial'] = ('extract', *FINANCIAL_EXTRACT)
EXPERT_METHODS['expert_market_research'] = ('search', 'market-research-v5',
    '研究市场问题，查阅可靠来源，结合地域、时间和统计口径形成结论。')


def published_experts():
    from ..routing.models import ExpertCandidate
    return tuple(ExpertCandidate(id=key, kind=kind,
        description=('财务字段逐项核对与提取' if key == 'expert_extract_financial' else
                     '公开网页市场调研、来源抓取与核对' if key == 'expert_market_research' else
                     f'{kind} research using the published {method} method'),
        tools=(CODING_TOOLS if kind == 'coding'
               else frozenset({'read_source','search_source','search_web','fetch_web'})),
        method_version=method, method_digest=hashlib.sha256(instruction.encode()).hexdigest())
        for key, (kind, method, instruction) in EXPERT_METHODS.items())
