from agents import agent7


def test_validate_candidate_delegates_blocked_sanctions(monkeypatch):
    candidate = {
        "supplier": "Iran",
        "grade": "Iranian Heavy",
        "refinery": "Jamnagar RIL",
        "proposed_volume_mbd": 0.2,
    }

    expected = {
        "status": "BLOCKED",
        "reason": {
            "rule": "OFAC_SDN",
            "value": "Iran",
            "threshold": None,
            "source": "OFAC SDN",
        },
        "adjusted_volume_mbd": 0.0,
    }

    monkeypatch.setattr(agent7, "validator", lambda candidate, playbook_id=None, tracker=None: expected)

    result = agent7.validate_candidate(candidate, playbook_id=None, tracker=None)

    assert result["status"] == "BLOCKED"
    assert result["reason"] is not None
    assert result["reason"]["rule"] == "OFAC_SDN"
    assert result["adjusted_volume_mbd"] == 0.0


def test_validate_candidate_delegates_blocked_grade(monkeypatch):
    candidate = {
        "supplier": "Venezuela",
        "grade": "Venezuelan Merey",
        "refinery": "Kochi BPCL",
        "proposed_volume_mbd": 0.2,
    }

    expected = {
        "status": "BLOCKED",
        "reason": {
            "rule": "GRADE_INCOMPATIBLE",
            "value": "Venezuelan Merey",
            "threshold": "Kochi BPCL",
            "source": "Neo4j",
        },
        "adjusted_volume_mbd": 0.0,
    }

    monkeypatch.setattr(agent7, "validator", lambda candidate, playbook_id=None, tracker=None: expected)

    result = agent7.validate_candidate(candidate, playbook_id=None, tracker=None)

    assert result["status"] == "BLOCKED"
    assert result["reason"]["rule"] == "GRADE_INCOMPATIBLE"
    assert result["reason"]["value"] == "Venezuelan Merey"


def test_validate_candidate_delegates_blocked_diversification(monkeypatch):
    candidate = {
        "supplier": "Russia",
        "grade": "Urals",
        "refinery": "Vadinar Nayara",
        "proposed_volume_mbd": 0.2,
    }

    expected = {
        "status": "BLOCKED",
        "reason": {
            "rule": "DIVERSIFICATION_CAP",
            "value": 0.41,
            "threshold": agent7.MAX_SUPPLIER_SHARE_PCT,
            "source": "Tracker",
        },
        "adjusted_volume_mbd": 0.0,
    }

    monkeypatch.setattr(agent7, "validator", lambda candidate, playbook_id=None, tracker=None: expected)

    result = agent7.validate_candidate(candidate, playbook_id=None, tracker=None)

    assert result["status"] == "BLOCKED"
    assert result["reason"]["rule"] == "DIVERSIFICATION_CAP"
    assert result["reason"]["threshold"] == agent7.MAX_SUPPLIER_SHARE_PCT


def test_validate_candidate_delegates_partial(monkeypatch):
    candidate = {
        "supplier": "UAE",
        "grade": "Murban",
        "refinery": "Kochi BPCL",
        "proposed_volume_mbd": 0.2,
    }

    expected = {
        "status": "PARTIAL",
        "reason": {
            "rule": "PORT_REF_CAPACITY",
            "value": 0.2,
            "threshold": 0.093,
            "source": "Refinery limits",
        },
        "adjusted_volume_mbd": 0.093,
    }

    monkeypatch.setattr(agent7, "validator", lambda candidate, playbook_id=None, tracker=None: expected)

    result = agent7.validate_candidate(candidate, playbook_id=None, tracker=None)

    assert result["status"] == "PARTIAL"
    assert result["reason"] is not None
    assert result["reason"]["rule"] == "PORT_REF_CAPACITY"
    assert result["adjusted_volume_mbd"] == 0.093


def test_validate_candidate_delegates_approved(monkeypatch):
    candidate = {
        "supplier": "USA",
        "grade": "WTI",
        "refinery": "Jamnagar RIL",
        "proposed_volume_mbd": 0.1,
    }

    expected = {
        "status": "APPROVED",
        "reason": None,
        "adjusted_volume_mbd": 0.1,
    }

    monkeypatch.setattr(agent7, "validator", lambda candidate, playbook_id=None, tracker=None: expected)

    result = agent7.validate_candidate(candidate, playbook_id=None, tracker=None)

    assert result["status"] == "APPROVED"
    assert result["reason"] is None
    assert result["adjusted_volume_mbd"] == 0.1