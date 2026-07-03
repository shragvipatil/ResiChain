"""
backend/scripts/seed_chroma.py

Seed ChromaDB collection `disruptionreports` with historical disruption summaries
for Agent 2 RAG grounding.

This script is idempotent because db.chroma_client uses:
- collection.upsert(...)
- SHA-256(content) as document IDs
"""

from __future__ import annotations

import logging

from db.chroma_client import init_chroma, seed_historical_events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


HISTORICAL_EVENTS = [
    {
        "date": "2003-03-20",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "War risk surged across Gulf energy routes and traders priced in supply disruption risk.",
        "text": "The 2003 Iraq war sharply increased geopolitical risk across Gulf shipping lanes. Oil markets reacted to fears of supply disruption, tanker insecurity, and broader regional escalation affecting export reliability."
    },
    {
        "date": "2011-02-20",
        "corridor": "Suez",
        "severity": "high",
        "outcome": "Libyan civil conflict removed significant crude supply and forced buyers to rebalance procurement.",
        "text": "The 2011 Libya conflict disrupted crude exports into Mediterranean markets and increased stress across regional shipping and refining networks. Importers faced sudden supply replacement pressure and elevated price volatility."
    },
    {
        "date": "2011-03-01",
        "corridor": "Suez",
        "severity": "medium",
        "outcome": "Regional instability during the Arab Spring increased maritime and energy trade uncertainty.",
        "text": "Arab Spring instability in North Africa and the Middle East raised concern over Suez-linked maritime flows, transit reliability, and exposure of oil supply chains to political unrest."
    },
    {
        "date": "2014-06-15",
        "corridor": "Hormuz",
        "severity": "medium",
        "outcome": "Iraq conflict risk increased concerns over regional export reliability and market balances.",
        "text": "The 2014 ISIS advance in Iraq increased market fears around oil infrastructure security, export continuity, and the broader resilience of Gulf energy corridors."
    },
    {
        "date": "2015-03-26",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Conflict near Bab-el-Mandeb raised shipping risk and increased insurance concerns for vessels.",
        "text": "The Yemen conflict intensified security concerns near the Bab-el-Mandeb chokepoint. Commercial shipping and energy markets began factoring in higher disruption risk for Red Sea transit."
    },
    {
        "date": "2016-10-09",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Missile threats near Bab-el-Mandeb highlighted vulnerability of commercial transit routes.",
        "text": "Missile incidents near the Bab-el-Mandeb strait demonstrated the vulnerability of one of the world’s most important maritime chokepoints for oil and container trade."
    },
    {
        "date": "2017-11-04",
        "corridor": "Hormuz",
        "severity": "medium",
        "outcome": "Regional Saudi-Iran tensions pushed oil risk premium higher.",
        "text": "Escalating Saudi-Iran tensions in late 2017 increased concerns around Gulf stability, energy export security, and the possibility of disruption affecting the Strait of Hormuz."
    },
    {
        "date": "2018-05-08",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Sanctions risk on Iran increased shipping uncertainty and regional supply concerns.",
        "text": "The reimposition of US sanctions on Iran heightened uncertainty over Gulf crude flows, tanker routing, and replacement sourcing for major import-dependent economies."
    },
    {
        "date": "2019-05-12",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Sabotage incidents near Fujairah raised fears of tanker vulnerability close to Hormuz.",
        "text": "Attacks on tankers near the UAE port of Fujairah signaled elevated risks for vessels operating near the Strait of Hormuz, increasing insurance costs and security concerns."
    },
    {
        "date": "2019-06-13",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Tanker attacks in the Gulf of Oman increased fears of escalation around Hormuz.",
        "text": "The June 2019 tanker attacks in the Gulf of Oman intensified fears that commercial shipping linked to Hormuz could be disrupted by military escalation and reprisals."
    },
    {
        "date": "2019-07-19",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "The seizure of a tanker underlined direct maritime coercion risk in the Gulf.",
        "text": "Iran’s seizure of the Stena Impero highlighted the growing risk of state-linked interference with commercial tanker operations tied to the Strait of Hormuz."
    },
    {
        "date": "2019-09-14",
        "corridor": "Hormuz",
        "severity": "critical",
        "outcome": "The Abqaiq-Khurais attack temporarily disrupted major Saudi oil processing capacity and triggered a sharp price spike.",
        "text": "The 2019 Abqaiq-Khurais attack on Saudi energy infrastructure demonstrated how a sudden strike on core oil processing assets could sharply affect global supply expectations, crude prices, and regional export risk."  # [web:162]
    },
    {
        "date": "2020-01-03",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "US-Iran confrontation increased fears of retaliatory disruption in Gulf shipping lanes.",
        "text": "The January 2020 US-Iran crisis after the killing of Qasem Soleimani caused immediate concern that the Strait of Hormuz could become the focus of retaliatory attacks or shipping interference."
    },
    {
        "date": "2021-03-23",
        "corridor": "Suez",
        "severity": "critical",
        "outcome": "The Ever Given blockage halted canal traffic and delayed global trade flows for days.",
        "text": "The 2021 Suez Canal obstruction by the Ever Given demonstrated how a single incident could block a major global trade artery, delay oil and goods shipments, and force expensive rerouting decisions."  # [web:164]
    },
    {
        "date": "2021-03-29",
        "corridor": "Cape",
        "severity": "medium",
        "outcome": "Shippers evaluated Cape rerouting as the main alternative to the blocked Suez Canal.",
        "text": "During the Suez blockage, the Cape of Good Hope emerged as the principal fallback route, but with longer transit times, higher fuel costs, and cascading scheduling delays."
    },
    {
        "date": "2021-07-29",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Tanker attack concerns renewed focus on Gulf maritime security exposure.",
        "text": "A tanker attack in 2021 renewed attention to the persistent vulnerability of commercial shipping linked to Gulf crude exports and the Strait of Hormuz."
    },
    {
        "date": "2022-02-24",
        "corridor": "Suez",
        "severity": "high",
        "outcome": "The Russia-Ukraine war pushed oil prices higher and forced major energy trade realignment.",
        "text": "The outbreak of the Russia-Ukraine war triggered major global energy market disruption, rerouted crude flows, and increased strain on alternative shipping and refining arrangements."
    },
    {
        "date": "2022-03-10",
        "corridor": "Cape",
        "severity": "medium",
        "outcome": "Trade realignment increased longer-haul routing and freight complexity.",
        "text": "Following sanctions and market fragmentation in 2022, crude and product trade patterns shifted toward longer-haul voyages, raising freight costs and increasing dependence on resilient fallback routes."
    },
    {
        "date": "2023-10-19",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Regional war spillover risk increased caution for Red Sea shipping.",
        "text": "The widening regional security fallout after the Israel-Gaza war increased concern that Red Sea and Bab-el-Mandeb maritime traffic could become exposed to politically motivated attacks."
    },
    {
        "date": "2023-11-19",
        "corridor": "RedSea",
        "severity": "critical",
        "outcome": "The seizure of Galaxy Leader marked a major escalation in the Houthi threat to shipping.",
        "text": "The seizure of the Galaxy Leader by Houthi forces in November 2023 marked a major escalation in the Red Sea crisis and highlighted the direct exposure of commercial shipping to militant action."  # [web:157]
    },
    {
        "date": "2023-12-03",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Repeated attacks on commercial vessels increased war-risk premiums and route uncertainty.",
        "text": "By early December 2023, repeated Houthi drone and missile attacks on commercial shipping in the Red Sea had increased insurance costs and forced carriers to reassess route safety."  # [web:157][web:169]
    },
    {
        "date": "2023-12-18",
        "corridor": "RedSea",
        "severity": "critical",
        "outcome": "Major shipping firms and BP paused or diverted Red Sea transits, increasing delays and costs.",
        "text": "In December 2023, major shipping lines and BP suspended or diverted Red Sea and Suez-linked transits because of Houthi attacks, shifting vessels toward the Cape route and lengthening voyage times."  # [web:157][web:169]
    },
    {
        "date": "2023-12-19",
        "corridor": "Cape",
        "severity": "high",
        "outcome": "Diversions around the Cape became the main resilience response to Red Sea insecurity.",
        "text": "As attacks intensified in the southern Red Sea, many ship operators rerouted around the Cape of Good Hope, accepting additional time and cost to avoid missile and boarding threats."  # [web:157][web:169]
    },
    {
        "date": "2024-01-02",
        "corridor": "RedSea",
        "severity": "critical",
        "outcome": "UN officials warned that attacks on shipping threatened maritime safety and supply chains.",
        "text": "By January 2024, attacks involving vessels such as MSC United and Maersk Hangzhou had elevated the Red Sea crisis into a broader supply chain and maritime security emergency."  # [web:167]
    },
    {
        "date": "2024-01-12",
        "corridor": "RedSea",
        "severity": "critical",
        "outcome": "International military strikes underscored that the Red Sea disruption had become a major geopolitical crisis.",
        "text": "The launch of retaliatory strikes against Houthi targets in January 2024 showed that Red Sea shipping disruption had escalated beyond isolated incidents into a full geopolitical crisis affecting global trade."
    },
    {
        "date": "2024-02-01",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Sustained insecurity kept freight costs elevated and prolonged Cape diversions.",
        "text": "Prolonged attacks in the Red Sea during early 2024 kept carriers on longer Cape routes, increasing freight expense, voyage time, and scheduling instability for importers."
    },
    {
        "date": "2024-03-17",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Analyses highlighted broad impacts of the Red Sea crisis on global shipping and energy trade.",
        "text": "By March 2024, policy and shipping analyses described the Red Sea crisis as a major disruption to global logistics, with clear spillovers into fuel costs, insurance premiums, and delivery schedules."  # [web:160]
    },
    {
        "date": "2024-04-01",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "The crisis persisted long enough to normalize large-scale rerouting decisions.",
        "text": "By April 2024, sustained insecurity in the Bab-el-Mandeb corridor had normalized Cape rerouting for many operators, demonstrating the long-tail cost of persistent maritime threat."
    },
    {
        "date": "2019-01-10",
        "corridor": "RedSea",
        "severity": "medium",
        "outcome": "Early Houthi-linked maritime threats signaled a recurring vulnerability in the corridor.",
        "text": "Early warning signs from Houthi-linked threats to maritime traffic indicated that the Red Sea and Bab-el-Mandeb corridor could become a recurring point of strategic shipping disruption."
    },
    {
        "date": "2020-06-15",
        "corridor": "Hormuz",
        "severity": "medium",
        "outcome": "US-Iran friction continued to sustain a security premium around Gulf exports.",
        "text": "Even without full closure, persistent US-Iran friction in 2020 kept a structural risk premium on Gulf energy exports and tanker routes associated with Hormuz."
    },
    {
        "date": "2021-05-01",
        "corridor": "Suez",
        "severity": "medium",
        "outcome": "Post-blockage recovery still exposed fragility in tightly optimized shipping systems.",
        "text": "After the Ever Given was refloated, supply chains remained disrupted as congestion, backlog, and scheduling slippage continued to affect Suez-dependent trade networks."
    },
    {
        "date": "2022-06-01",
        "corridor": "Hormuz",
        "severity": "medium",
        "outcome": "Energy sanctions and regional rivalry kept Gulf route risk elevated.",
        "text": "Persistent sanctions pressure and geopolitical rivalry in the Gulf during 2022 sustained uncertainty around reliable oil transit and emergency sourcing for importers."
    },
    {
        "date": "2023-12-25",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Holiday-period attacks reinforced that the threat was sustained rather than isolated.",
        "text": "Continued attacks on shipping through late December 2023 demonstrated that Red Sea disruption had become persistent, not episodic, with consequences for route planning and energy logistics."
    },
    {
        "date": "2024-01-20",
        "corridor": "Cape",
        "severity": "medium",
        "outcome": "Cape rerouting became operationally familiar but remained materially more expensive.",
        "text": "The Cape route provided continuity when Red Sea transit became unsafe, but it imposed substantial increases in distance, cost, vessel utilization, and delivery lead time."
    },
    {
        "date": "2024-02-20",
        "corridor": "Suez",
        "severity": "medium",
        "outcome": "Reduced Red Sea traffic weakened normal Suez-linked throughput and scheduling assumptions.",
        "text": "As Red Sea insecurity persisted in 2024, the knock-on effects reduced confidence in Suez-linked scheduling and highlighted the fragility of chokepoint-dependent maritime planning."
    },
    {
        "date": "2018-07-25",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Saudi-related shipping suspensions showed Bab-el-Mandeb risk could affect oil exports directly.",
        "text": "A temporary suspension of some Saudi oil shipments through Bab-el-Mandeb in 2018 showed that security incidents in the corridor could rapidly affect energy routing decisions."
    },
    {
        "date": "2019-10-01",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Post-Abqaiq reassessment raised the perceived vulnerability of concentrated oil infrastructure.",
        "text": "After the Abqaiq attack, analysts and importers reassessed the vulnerability of concentrated energy infrastructure and the knock-on implications for export reliability and price stability."
    },
    {
        "date": "2023-11-30",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Attack frequency and uncertainty pushed carriers toward risk avoidance decisions.",
        "text": "As attacks and attempted boardings increased in late 2023, commercial operators began prioritizing route avoidance over shorter transit through the Red Sea corridor."  # [web:157]
    },
    {
        "date": "2024-03-01",
        "corridor": "Cape",
        "severity": "medium",
        "outcome": "Fallback routing preserved continuity but strained fleet efficiency and schedules.",
        "text": "Fallback routing around the Cape of Good Hope helped preserve supply continuity during Red Sea disruption, but it reduced fleet efficiency and stretched voyage planning."
    },
    {
        "date": "2021-04-05",
        "corridor": "Suez",
        "severity": "medium",
        "outcome": "The Suez blockage reinforced the strategic value of route diversification and resilience planning.",
        "text": "The aftermath of the Ever Given incident underscored how dependent supply chains were on uninterrupted Suez transit and how limited immediate alternatives could be."
    },
    {
        "date": "2019-09-16",
        "corridor": "Hormuz",
        "severity": "critical",
        "outcome": "Oil prices reacted sharply after Abqaiq, proving how infrastructure attacks can amplify market shocks.",
        "text": "The immediate oil price reaction after the Abqaiq-Khurais attack illustrated how attacks on strategically important energy assets can trigger outsized market responses."  # [web:162]
    },
    {
        "date": "2024-04-15",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Renewed Iran-Israel tensions revived concern over closure threats and tanker security in the Gulf.",
        "text": "Renewed military tension involving Iran in 2024 revived long-standing fears that escalation could spill into Gulf shipping lanes and disrupt energy exports tied to Hormuz."
    },
    {
        "date": "2011-08-01",
        "corridor": "Suez",
        "severity": "medium",
        "outcome": "Mediterranean and North African instability complicated regional crude flow assumptions.",
        "text": "As the Libya conflict continued in 2011, refiners and importers had to manage uncertainty in Mediterranean supply availability and evolving shipping patterns."
    },
    {
        "date": "2015-04-10",
        "corridor": "RedSea",
        "severity": "medium",
        "outcome": "Conflict spillover made Bab-el-Mandeb a recognized energy chokepoint risk.",
        "text": "The Yemen war made Bab-el-Mandeb a more prominent strategic chokepoint in global energy risk analysis, especially for crude and refined product flows."
    },
    {
        "date": "2023-12-10",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Threat escalation started changing commercial operator behavior before full-scale diversion took hold.",
        "text": "By mid-December 2023, shipping operators were already adjusting routes, schedules, and risk assumptions in response to escalating Houthi attacks in the Red Sea."  # [web:169]
    },
    {
        "date": "2024-01-05",
        "corridor": "RedSea",
        "severity": "critical",
        "outcome": "The operational threat environment remained active despite naval responses.",
        "text": "Even after multinational naval responses, the Red Sea threat environment remained active in early 2024, sustaining caution among carriers and energy traders."
    },
    {
        "date": "2024-02-10",
        "corridor": "Cape",
        "severity": "medium",
        "outcome": "Longer voyages affected tanker availability and increased effective shipping tightness.",
        "text": "Cape rerouting in early 2024 tied up vessels for longer durations, reducing effective tanker availability and increasing freight pressure across energy supply chains."
    },
    {
        "date": "2024-03-10",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Persistent attacks created a durable benchmark case for maritime disruption scenario planning.",
        "text": "The prolonged Red Sea crisis became a benchmark modern case of how repeated low-cost attacks can generate major logistical and energy supply chain disruption."
    },
    {
        "date": "2024-04-20",
        "corridor": "Cape",
        "severity": "medium",
        "outcome": "Supply continuity via rerouting came with measurable cost and timing penalties.",
        "text": "Rerouting around the Cape preserved some continuity for global trade and oil cargoes, but the penalty in cost, transit time, and schedule reliability remained substantial."
    },
    {
        "date": "2019-06-20",
        "corridor": "Hormuz",
        "severity": "high",
        "outcome": "Military confrontation risk near the Gulf raised fears of broader disruption.",
        "text": "Military escalation signals around the Gulf in 2019 reinforced the risk that localized incidents could spill into a wider Hormuz disruption scenario."
    },
    {
        "date": "2023-11-25",
        "corridor": "RedSea",
        "severity": "high",
        "outcome": "Boarding and seizure risk changed the security profile for merchant shipping.",
        "text": "The growing risk of boarding, seizure, missile attack, and drone attack in late 2023 transformed the Red Sea into a much higher-risk corridor for merchant vessels."  # [web:157]
    },
    {
        "date": "2021-03-24",
        "corridor": "Suez",
        "severity": "critical",
        "outcome": "Traffic suspension through Suez created immediate global scheduling shock.",
        "text": "When the Ever Given blocked the Suez Canal in March 2021, shipping schedules, cargo delivery timelines, and confidence in uninterrupted canal access were immediately disrupted."  # [web:164]
    },
]


def main() -> None:
    init_chroma()
    total = seed_historical_events(HISTORICAL_EVENTS)
    print(f"Chroma seed complete. Collection count: {total}")


if __name__ == "__main__":
    main()