from db.postgres_queries import check_ofac_match, is_comprehensively_sanctioned_country
from db.neo4j_queries import check_grade_compatibility

countries = ["Russia", "Saudi Arabia", "UAE", "Iraq", "Iran"]
for s in countries:
    print(s, "-> check_ofac_match:", check_ofac_match(s), "| country_embargo:", is_comprehensively_sanctioned_country(s))

print()
print("Venezuela Merey/Kochi grade check (should be False = incompatible):",
      check_grade_compatibility("Merey", "Kochi BPCL"))
print("Venezuela OFAC entity check (should now be False, no fake row):", check_ofac_match("Venezuela"))
