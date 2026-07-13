"""Tests for the fixed Cypher template library (app/core/cypher_library.py)."""

from app.core.cypher_library import match_question


def test_founders_of_company():
    m = match_question("Who founded NovaPay?")
    assert m.template_id == "founders_of_company"
    assert m.params == {"name": "NovaPay"}
    assert "LIMIT $limit" in m.cypher


def test_investor_portfolio():
    m = match_question("What companies has Sequoia Trail invested in and how much?")
    assert m.template_id == "investor_portfolio"
    assert m.params == {"name": "Sequoia Trail"}


def test_sector_valuation_over_threshold():
    m = match_question("Which fintech companies are valued over $10 billion?")
    assert m.template_id == "companies_by_sector_and_valuation"
    assert m.params == {"sector": "fintech", "amount": 10.0}
    assert "c.valuation_billion > $amount" in m.cypher


def test_sector_valuation_under_threshold_uses_less_than_operator():
    m = match_question("Which cleantech companies are valued under 5 billion?")
    assert m.template_id == "companies_by_sector_and_valuation"
    assert m.params == {"sector": "cleantech", "amount": 5.0}
    assert "c.valuation_billion < $amount" in m.cypher


def test_current_executives():
    m = match_question("Who are the current executives at SecureLayer?")
    assert m.template_id == "current_team_at_company"
    assert m.params == {"name": "SecureLayer"}


def test_person_profile():
    m = match_question("What has Marcus Chen founded and won awards for?")
    assert m.template_id == "person_profile"
    assert m.params == {"name": "Marcus Chen"}


def test_top_acquirers_no_params():
    m = match_question("Which company made the most acquisitions?")
    assert m.template_id == "top_acquirers"
    assert m.params == {}


def test_who_acquired_takes_priority_over_generic_profile():
    m = match_question("Who acquired SecureLayer?")
    assert m.template_id == "who_acquired_company"
    assert m.params == {"name": "SecureLayer"}


def test_acquisitions_by_company_handles_trailing_question_mark():
    m = match_question("What did DataForge acquire?")
    assert m.template_id == "acquisitions_by_company"
    assert m.params == {"name": "DataForge"}


def test_serial_founders_aggregate():
    m = match_question("Who are the serial founders?")
    assert m.template_id == "serial_founders"


def test_company_products():
    m = match_question("What products does DataForge develop?")
    assert m.template_id == "company_products"
    assert m.params == {"name": "DataForge"}


def test_company_investors():
    m = match_question("Which investors backed GreenGrid?")
    assert m.template_id == "company_investors"
    assert m.params == {"name": "GreenGrid"}


def test_entity_awards():
    m = match_question("What awards has NovaPay won?")
    assert m.template_id == "entity_awards"
    assert m.params == {"name": "NovaPay"}


def test_companies_founded_in_year():
    m = match_question("Which companies were founded in 2016?")
    assert m.template_id == "companies_founded_in_year"
    assert m.params == {"year": 2016}


def test_tell_me_about_routes_to_company_profile():
    m = match_question("Tell me about NovaPay.")
    assert m.template_id == "company_profile"
    assert m.params == {"name": "NovaPay"}


def test_who_is_routes_to_person_profile():
    m = match_question("Who is Marcus Chen?")
    assert m.template_id == "person_profile"
    assert m.params == {"name": "Marcus Chen"}


def test_unrecognized_question_returns_none():
    assert match_question("asdkjaslkdjaslkd random gibberish") is None


def test_every_template_cypher_is_read_only_and_limited():
    from app.core.cypher_library import TEMPLATES
    from app.core.graph_rag import validate_cypher

    for template in TEMPLATES:
        # The valuation template builds its query dynamically at match
        # time, so exercise it through a real match rather than the
        # (empty) placeholder on the template object itself.
        if template.id == "companies_by_sector_and_valuation":
            match = match_question("Which fintech companies are valued over $10 billion?")
            cypher = match.cypher
        else:
            cypher = template.cypher
        result = validate_cypher(cypher)
        assert result.valid, f"{template.id} failed validation: {result.reason}"
