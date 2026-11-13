"""Role templates must stay company-neutral, or they will work on one customer and break on the next."""

import json
import re
from pathlib import Path

from agentforge_core.business_concepts import CONCEPTS
from agentforge_core.role_templates import TEMPLATES

DICTIONARY = Path(__file__).resolve().parents[3] / "tests" / "brightline_dictionary.json"
# "1.8%", "Rs 5000", "INR 200", "5,00,000" - a hard-coded limit or amount
HARD_CODED_LIMIT = re.compile(r"\d+(\.\d+)?[ \t]*%|\b(rs|inr)\.?[ \t]*\d|\d{2,},\d{2,}")


def _company_names():
    """Table and column identifiers a real company dictionary uses (Brightline here)."""
    if not DICTIONARY.exists():
        return set()
    concepts = json.loads(DICTIONARY.read_text(encoding="utf-8"))["concepts"]
    names = {entry["table"].lower() for entry in concepts.values()}
    for entry in concepts.values():
        names |= {c.lower() for c in entry.get("columns", {}).values() if re.fullmatch(r"[a-z_]+", c.lower())}
    # plain English words ("customers", "invoices", "name") are fine; identifiers like receipt_allocations are not
    return {n for n in names if "_" in n}


def test_templates_only_use_standard_business_concepts():
    for template in TEMPLATES.values():
        assert set(template["concepts"]) <= set(CONCEPTS), template["id"]


def test_templates_never_mention_one_companys_tables_or_columns():
    company_specific = _company_names() - set(CONCEPTS)  # concept names themselves are fine
    for template in TEMPLATES.values():
        words = set(re.findall(r"[a-z_]+", template["instructions"].lower()))
        leaked = sorted(company_specific & words)
        assert not leaked, f"{template['id']} names company tables/columns: {leaked} - move them to the data dictionary"


def test_templates_carry_no_hard_coded_limits():
    """Limits like 'Rs 5,00,000' or '1.8%' belong to company policies, not the role."""
    assert HARD_CODED_LIMIT.search("approval above rs 5,00,000 and a fee of 1.8%")  # the check itself works
    for template in TEMPLATES.values():
        assert not HARD_CODED_LIMIT.search(template["instructions"].lower()), template["id"]
