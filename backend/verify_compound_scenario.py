import sys
sys.path.insert(0, ".")

from agents.simulation import run_all

supplier_route_risks = [
    {"supplier": "Saudi Arabia", "primary_chokepoint": "Strait of Hormuz", "import_share": 0.182, "route_risk": 1.0},
    {"supplier": "Iraq", "primary_chokepoint": "Strait of Hormuz", "import_share": 0.221, "route_risk": 1.0},
    {"supplier": "UAE", "primary_chokepoint": "Strait of Hormuz", "import_share": 0.084, "route_risk": 1.0},
    {"supplier": "Kuwait", "primary_chokepoint": "Strait of Hormuz", "import_share": 0.068, "route_risk": 1.0},
    {"supplier": "Russia", "primary_chokepoint": "Bab-el-Mandeb", "import_share": 0.213, "route_risk": 1.0},
]

result = run_all(
    supplier_route_risks=supplier_route_risks,
    closure_severity={"Strait of Hormuz": 0.82, "Bab-el-Mandeb": 0.87},
    affected_chokepoint=["Strait of Hormuz", "Bab-el-Mandeb"],
)

print("=== DISRUPTION ===")
print(result["disruption"])
print()
print("=== PRICE ===")
print(result["price"])
print()
print("=== REFINERIES ===")
for r in result["refineries"]:
    print(r["refinery_name"], "-> util_delta_pct:", r.get("util_delta_pct"), "| new_utilization_pct:", r.get("new_utilization_pct"))
print()
print("=== META ===")
print(result["meta"])