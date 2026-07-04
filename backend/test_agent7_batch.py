from agents.agent7 import validate_batch

candidates = [
    {
        "option_id": "test_russia_001",
        "supplier": "Russia",
        "grade": "Urals",
        "refinery": "Jamnagar RIL",
        "arrival_port": "Vadinar",
        "departure_port": "Nonexistent Port",
        "vessel_class": "VLCC",
        "proposed_volume_mbd": 0.20,
        "confidence": 0.91,
    }
]

results = validate_batch(candidates, playbook_id=None)
print("\n=== Agent 7 validate_batch result ===")
for row in results:
    print(row)