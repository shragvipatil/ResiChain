# ============================================================
# ResiChain — Neo4j Client
# Knowledge Graph: suppliers, routes, chokepoints, refineries
# ============================================================

from neo4j import AsyncGraphDatabase
import os
import logging

logger = logging.getLogger(__name__)

_driver = None  # Global Neo4j driver

async def get_neo4j_driver():
    """Returns the global Neo4j async driver."""
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
            auth=(
                os.getenv("NEO4J_USER", "neo4j"),
                os.getenv("NEO4J_PASSWORD", "resichain_neo4j")
            )
        )
    return _driver

async def init_neo4j():
    """
    Called on FastAPI startup.
    Verifies connection and seeds the Knowledge Graph
    with base data if it doesn't exist yet.
    """
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        # Verify connection
        await session.run("RETURN 1")
        logger.info("Neo4j connection verified")

        # Seed Knowledge Graph if empty
        result = await session.run("MATCH (n) RETURN count(n) as count")
        record = await result.single()
        if record["count"] == 0:
            await seed_knowledge_graph(session)
            logger.info("Knowledge Graph seeded with base data")
        else:
            logger.info(f"Knowledge Graph already has {record['count']} nodes")

async def seed_knowledge_graph(session):
    """
    Seeds the Neo4j Knowledge Graph with:
    - 6 supplier countries with import shares
    - 6 crude grades
    - 4 chokepoints
    - 5 Indian ports
    - 4 Indian refineries
    - 3 SPR storage sites
    - Grade compatibility relationships
    - Route and chokepoint relationships
    
    Sources: UN Comtrade, MoPNG, PPAC, EIA
    """

    # ---- Constraints (prevent duplicates) -------------------
    await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:Supplier) REQUIRE s.name IS UNIQUE")
    await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chokepoint) REQUIRE c.name IS UNIQUE")
    await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (r:Refinery) REQUIRE r.name IS UNIQUE")
    await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (g:CrudeGrade) REQUIRE g.name IS UNIQUE")
    await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:Port) REQUIRE p.name IS UNIQUE")

    # ---- Suppliers (Source: UN Comtrade + PPAC 2023-24) -----
    suppliers = [
        {"name": "Saudi Arabia", "import_share_pct": 18, "country": "SA", "primary_grade": "Arab Light"},
        {"name": "Iraq",         "import_share_pct": 22, "country": "IQ", "primary_grade": "Basra Light"},
        {"name": "Russia",       "import_share_pct": 22, "country": "RU", "primary_grade": "Urals"},
        {"name": "UAE",          "import_share_pct": 8,  "country": "AE", "primary_grade": "Murban"},
        {"name": "USA",          "import_share_pct": 8,  "country": "US", "primary_grade": "WTI"},
        {"name": "Kuwait",       "import_share_pct": 6,  "country": "KW", "primary_grade": "Kuwait Export"},
    ]
    for s in suppliers:
        await session.run("""
            MERGE (s:Supplier {name: $name})
            SET s.import_share_pct = $import_share_pct,
                s.country = $country,
                s.primary_grade = $primary_grade
        """, **s)

    # ---- Crude Grades (Source: EIA grade specifications) ----
    grades = [
        {"name": "Arab Light",     "api_gravity": 32.8, "sulfur_pct": 1.77, "type": "medium_sour"},
        {"name": "Basra Light",    "api_gravity": 29.0, "sulfur_pct": 2.90, "type": "medium_sour"},
        {"name": "Murban",         "api_gravity": 40.5, "sulfur_pct": 0.78, "type": "light_sweet"},
        {"name": "Urals",          "api_gravity": 31.7, "sulfur_pct": 1.35, "type": "medium_sour"},
        {"name": "WTI",            "api_gravity": 39.6, "sulfur_pct": 0.24, "type": "light_sweet"},
        {"name": "Kuwait Export",  "api_gravity": 31.0, "sulfur_pct": 2.52, "type": "medium_sour"},
    ]
    for g in grades:
        await session.run("""
            MERGE (g:CrudeGrade {name: $name})
            SET g.api_gravity = $api_gravity,
                g.sulfur_pct = $sulfur_pct,
                g.type = $type
        """, **g)

    # ---- Chokepoints (Source: EIA chokepoint database) ------
    chokepoints = [
        {"name": "Hormuz",    "daily_capacity_mbd": 21.0, "current_risk": 0.34},
        {"name": "Red_Sea",   "daily_capacity_mbd": 6.2,  "current_risk": 0.41},
        {"name": "Suez",      "daily_capacity_mbd": 5.5,  "current_risk": 0.18},
        {"name": "Cape",      "daily_capacity_mbd": 999.0, "current_risk": 0.05},
    ]
    for c in chokepoints:
        await session.run("""
            MERGE (c:Chokepoint {name: $name})
            SET c.daily_capacity_mbd = $daily_capacity_mbd,
                c.current_risk = $current_risk
        """, **c)

    # ---- Indian Ports (Source: OpenStreetMap + MoPNG) -------
    ports = [
        {"name": "Sikka",   "lat": 22.43, "lon": 69.82, "max_vessel_dwt": 320000},
        {"name": "Vadinar", "lat": 22.46, "lon": 69.77, "max_vessel_dwt": 300000},
        {"name": "Kochi",   "lat": 9.96,  "lon": 76.27, "max_vessel_dwt": 120000},
        {"name": "Paradip", "lat": 20.32, "lon": 86.67, "max_vessel_dwt": 150000},
        {"name": "Vizag",   "lat": 17.69, "lon": 83.28, "max_vessel_dwt": 150000},
    ]
    for p in ports:
        await session.run("""
            MERGE (p:Port {name: $name})
            SET p.lat = $lat, p.lon = $lon,
                p.max_vessel_dwt = $max_vessel_dwt
        """, **p)

    # ---- Refineries (Source: MoPNG + IOCL/HPCL/BPCL Reports)
    refineries = [
        {"name": "Jamnagar RIL",  "owner": "RIL",   "capacity_mbd": 1.24, "has_coker": True},
        {"name": "Vadinar Nayara","owner": "Nayara", "capacity_mbd": 0.40, "has_coker": True},
        {"name": "Kochi BPCL",   "owner": "BPCL",   "capacity_mbd": 0.31, "has_coker": False},
        {"name": "Paradip IOCL", "owner": "IOCL",   "capacity_mbd": 0.30, "has_coker": True},
    ]
    for r in refineries:
        await session.run("""
            MERGE (r:Refinery {name: $name})
            SET r.owner = $owner,
                r.capacity_mbd = $capacity_mbd,
                r.has_coker = $has_coker
        """, **r)

    # ---- SPR Storage Sites (Source: PPAC SPR data) ----------
    spr_sites = [
        {"name": "Visakhapatnam SPR", "capacity_mb": 13.3, "type": "SPR"},
        {"name": "Mangalore SPR",     "capacity_mb": 11.3, "type": "SPR"},
        {"name": "Padur SPR",         "capacity_mb": 18.6, "type": "SPR"},
    ]
    for s in spr_sites:
        await session.run("""
            MERGE (sf:StorageFacility {name: $name})
            SET sf.capacity_mb = $capacity_mb, sf.type = $type
        """, **s)

    # ---- Relationships: Supplier PRODUCES CrudeGrade --------
    await session.run("""
        MATCH (s:Supplier {name: 'Saudi Arabia'}), (g:CrudeGrade {name: 'Arab Light'})
        MERGE (s)-[:PRODUCES]->(g)
    """)
    await session.run("""
        MATCH (s:Supplier {name: 'Iraq'}), (g:CrudeGrade {name: 'Basra Light'})
        MERGE (s)-[:PRODUCES]->(g)
    """)
    await session.run("""
        MATCH (s:Supplier {name: 'Russia'}), (g:CrudeGrade {name: 'Urals'})
        MERGE (s)-[:PRODUCES]->(g)
    """)
    await session.run("""
        MATCH (s:Supplier {name: 'UAE'}), (g:CrudeGrade {name: 'Murban'})
        MERGE (s)-[:PRODUCES]->(g)
    """)
    await session.run("""
        MATCH (s:Supplier {name: 'USA'}), (g:CrudeGrade {name: 'WTI'})
        MERGE (s)-[:PRODUCES]->(g)
    """)
    await session.run("""
        MATCH (s:Supplier {name: 'Kuwait'}), (g:CrudeGrade {name: 'Kuwait Export'})
        MERGE (s)-[:PRODUCES]->(g)
    """)

    # ---- Relationships: CrudeGrade COMPATIBLE_WITH Refinery -
    # Source: IOCL/HPCL/BPCL annual reports + refinery specs
    # KEY: Kochi has NO coker — cannot process heavy sour grades
    compatible = [
        ("Arab Light",    "Jamnagar RIL"),
        ("Arab Light",    "Kochi BPCL"),
        ("Arab Light",    "Paradip IOCL"),
        ("Arab Light",    "Vadinar Nayara"),
        ("Basra Light",   "Jamnagar RIL"),
        ("Basra Light",   "Paradip IOCL"),
        ("Basra Light",   "Vadinar Nayara"),
        ("Murban",        "Jamnagar RIL"),
        ("Murban",        "Vadinar Nayara"),
        ("Urals",         "Jamnagar RIL"),
        ("Urals",         "Paradip IOCL"),
        ("Urals",         "Vadinar Nayara"),
        ("WTI",           "Jamnagar RIL"),
        ("WTI",           "Kochi BPCL"),
        ("Kuwait Export", "Jamnagar RIL"),
        ("Kuwait Export", "Paradip IOCL"),
    ]
    for grade, refinery in compatible:
        await session.run("""
            MATCH (g:CrudeGrade {name: $grade}), (r:Refinery {name: $refinery})
            MERGE (g)-[:COMPATIBLE_WITH]->(r)
        """, grade=grade, refinery=refinery)

    # ---- Relationships: Supplier PASSES_THROUGH Chokepoint --
    # Saudi, Iraq, UAE, Kuwait all go through Hormuz
    hormuz_suppliers = ["Saudi Arabia", "Iraq", "UAE", "Kuwait"]
    for supplier in hormuz_suppliers:
        await session.run("""
            MATCH (s:Supplier {name: $supplier}), (c:Chokepoint {name: 'Hormuz'})
            MERGE (s)-[:SHIPS_VIA_CHOKEPOINT]->(c)
        """, supplier=supplier)

    # Russia (Baltic/Black Sea route) goes through Suez sometimes
    await session.run("""
        MATCH (s:Supplier {name: 'Russia'}), (c:Chokepoint {name: 'Suez'})
        MERGE (s)-[:SHIPS_VIA_CHOKEPOINT]->(c)
    """)

    # USA goes Cape route (no chokepoint dependency for India)
    await session.run("""
        MATCH (s:Supplier {name: 'USA'}), (c:Chokepoint {name: 'Cape'})
        MERGE (s)-[:SHIPS_VIA_CHOKEPOINT]->(c)
    """)

    logger.info("Knowledge Graph seeded successfully")


async def get_surviving_routes(blocked_chokepoints: list) -> list:
    """
    Agent 4 uses this.
    Returns suppliers whose routes do NOT pass through any blocked chokepoint.
    This is the core Knowledge Graph traversal.
    
    Example:
        blocked = ["Hormuz", "Red_Sea"]
        Returns: ["USA", "Russia"] (they don't use Hormuz or Red Sea)
    """
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run("""
            MATCH (s:Supplier)
            WHERE NOT (s)-[:SHIPS_VIA_CHOKEPOINT]->(:Chokepoint)
                WHERE (:Chokepoint).name IN $blocked_chokepoints
            RETURN s.name as supplier, s.import_share_pct as share
        """, blocked_chokepoints=blocked_chokepoints)
        records = await result.data()
        return records


async def get_compatible_refineries(grade_name: str) -> list:
    """
    Agent 7 uses this for grade compatibility check.
    Returns list of refinery names that can process the given crude grade.
    """
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run("""
            MATCH (g:CrudeGrade {name: $grade})-[:COMPATIBLE_WITH]->(r:Refinery)
            RETURN r.name as refinery, r.capacity_mbd as capacity
        """, grade=grade_name)
        records = await result.data()
        return records 