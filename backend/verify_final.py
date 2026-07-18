from db.postgres_queries import check_ofac_match, is_comprehensively_sanctioned_country

for s in ["Russia", "Saudi Arabia", "UAE", "Iraq", "Iran", "Islamic Republic of Iran"]:
    print(s, "-> check_ofac_match:", check_ofac_match(s), "| country_embargo:", is_comprehensively_sanctioned_country(s))
