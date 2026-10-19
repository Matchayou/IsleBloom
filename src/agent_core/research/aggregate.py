"""Scenario validation stays outside generic nodes and Core."""
from pydantic import Field
from ..contracts import Value


class CompanyPeriod(Value):
    company: str = Field(min_length=1)
    period: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    basis: str = Field(min_length=1)


def validate_comparison(companies):
    if len({v.company for v in companies}) != len(companies):
        raise ValueError('Duplicate company')
    if len({(v.period, v.currency, v.unit, v.basis) for v in companies}) != 1:
        raise ValueError('报告期、币种、单位或口径不同，不能直接比较')
    return tuple(companies)


def validate_source_comparison(scopes, scope, refs):
    """Typed financial records uploaded as UTF-8 JSON text. Other documents retain
    their native text and require the agent to state unverified/missing metadata.
    Values come from file bodies, never filenames or guessed company attributes.
    """
    import json
    companies = []
    for ref in refs:
        body = '\n'.join(f.text for f in scopes.read(scope, ref))
        try: value = json.loads(body)
        except (ValueError, TypeError): continue
        if isinstance(value, dict) and value.get('record_type') == 'company_financials':
            companies.append(CompanyPeriod.model_validate({k:value[k] for k in CompanyPeriod.model_fields}))
    if companies:
        # Multiple dimension results may refer to the same underlying company record.
        unique = tuple(dict.fromkeys(companies))
        validate_comparison(unique)
    return tuple(companies)
