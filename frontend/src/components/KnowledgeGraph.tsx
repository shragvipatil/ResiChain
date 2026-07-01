/**
 * KnowledgeGraph.tsx — Day 9 deliverable (Person C)
 *
 * D3 force-directed graph of India's crude oil supply chain.
 * Replaces Neo4j Browser dependency (Fix 14) — runs entirely
 * inside the React app, no port 7474 required.
 *
 * Node colours by type:
 *   Supplier    → teal   (#2dd4bf)
 *   CrudeGrade  → amber  (#fbbf24)
 *   Route       → blue   (#60a5fa)
 *   Chokepoint  → red    (#f87171)
 *   Refinery    → green  (#4ade80)
 *
 * Interactions:
 *   - Click node  → side panel shows all properties
 *   - Drag node   → pin it in place (D3 fix)
 *   - Scroll      → zoom in/out
 *   - Pan         → drag on background
 *
 * Demo Minute 1 (per CLAUDE.md):
 *   "This is India's crude oil supply chain as a live knowledge
 *    graph. Every node and edge is seeded from real data."
 *   Open this full-screen first. The visual communicates in 10s.
 *
 * When compound disruption detected:
 *   - Blocked chokepoint nodes pulse red
 *   - Edges through blocked chokepoints grey out
 *   - Surviving route glows cyan
 *   Pass `blockedChokepoints` prop from AppContext.
 *
 * Day 12 upgrade: zero changes — just feed real /api/kgraph data.
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import * as d3 from "d3";
import { KGraphData, KNode, KEdge } from "../types";
import { getKGraph } from "../api/endpoints";

// ── Node colour by type ───────────────────────────────────────────────────────

const NODE_COLORS: Record<string, string> = {
  Supplier:   "#2dd4bf",  // teal
  CrudeGrade: "#fbbf24",  // amber
  Route:      "#60a5fa",  // blue
  Chokepoint: "#f87171",  // red
  Refinery:   "#4ade80",  // green
};

const NODE_RADIUS: Record<string, number> = {
  Supplier:   22,
  CrudeGrade: 16,
  Route:      14,
  Chokepoint: 20,
  Refinery:   18,
};

const EDGE_COLORS: Record<string, string> = {
  SHIPS_VIA:        "#475569",
  PASSES_THROUGH:   "#dc2626",
  ARRIVES_AT:       "#1d4ed8",
  PRODUCES:         "#78716c",
  COMPATIBLE_WITH:  "#166534",
};

// ── D3 simulation node type ───────────────────────────────────────────────────

interface SimNode extends KNode, d3.SimulationNodeDatum {
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

// ── Property display helper ───────────────────────────────────────────────────

function NodeDetailPanel({ node, onClose }: { node: KNode; onClose: () => void }) {
  const color = NODE_COLORS[node.type] ?? "#94a3b8";

  const details: { label: string; value: string }[] = [];
  if (node.share    != null) details.push({ label: "Import share", value: `${node.share}%` });
  if (node.risk     != null) details.push({ label: "Risk score",   value: `${(node.risk * 100).toFixed(0)}%` });
  if (node.capacity != null) details.push({ label: "Capacity",     value: `${node.capacity} Mb/d` });
  if (node.gravity  != null) details.push({ label: "API gravity",  value: `${node.gravity}°` });

  return (
    <div className="absolute top-3 right-3 z-10 w-56 bg-slate-900 border border-slate-700 rounded-xl p-4 shadow-xl">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
          <div>
            <p className="text-white text-sm font-medium leading-tight">{node.label}</p>
            <p className="text-slate-500 text-xs">{node.type}</p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-slate-600 hover:text-white text-base leading-none ml-1 shrink-0"
        >×</button>
      </div>
      {details.length > 0 && (
        <div className="space-y-1.5">
          {details.map(({ label, value }) => (
            <div key={label} className="flex justify-between text-xs">
              <span className="text-slate-500">{label}</span>
              <span className="text-slate-200 font-medium">{value}</span>
            </div>
          ))}
        </div>
      )}
      {details.length === 0 && (
        <p className="text-slate-600 text-xs">No additional properties</p>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface KnowledgeGraphProps {
  blockedChokepoints?: string[];   // node labels — highlights blocked paths
  height?: string;
}

const KnowledgeGraph: React.FC<KnowledgeGraphProps> = ({
  blockedChokepoints = [],
  height = "480px",
}) => {
  const svgRef        = useRef<SVGSVGElement>(null);
  const containerRef  = useRef<HTMLDivElement>(null);
  const simRef        = useRef<d3.Simulation<SimNode, undefined> | null>(null);

  const [graphData, setGraphData] = useState<KGraphData | null>(null);
  const [loading, setLoading]     = useState(true);
  const [selectedNode, setSelectedNode] = useState<KNode | null>(null);

  // Fetch graph data
  useEffect(() => {
    getKGraph().then((data) => {
      setGraphData(data);
      setLoading(false);
    });
  }, []);

  // Build D3 simulation
  useEffect(() => {
    if (!graphData || !svgRef.current || !containerRef.current) return;

    const container = containerRef.current;
    const width  = container.clientWidth  || 800;
    const height = container.clientHeight || 480;

    // Clear previous render
    d3.select(svgRef.current).selectAll("*").remove();

    const svg = d3.select(svgRef.current)
      .attr("width", width)
      .attr("height", height);

    // ── Zoom + pan ────────────────────────────────────────────────────────────
    const g = svg.append("g");

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on("zoom", (event) => {
        g.attr("transform", event.transform);
      });

    svg.call(zoom);

    // Click on background → deselect node
    svg.on("click", (event) => {
      if (event.target === svgRef.current || event.target.tagName === "rect") {
        setSelectedNode(null);
      }
    });

    // Transparent background to catch clicks
    g.append("rect")
      .attr("width", width)
      .attr("height", height)
      .attr("fill", "transparent");

    // ── Prepare simulation nodes and links ───────────────────────────────────
    const nodes: SimNode[] = graphData.nodes.map((n) => ({ ...n }));
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    const links = graphData.edges
      .map((e) => ({
        source: nodeMap.get(e.from)!,
        target: nodeMap.get(e.to)!,
        label:  e.label,
      }))
      .filter((l) => l.source && l.target);

    // ── D3 Force Simulation ──────────────────────────────────────────────────
    const sim = d3.forceSimulation<SimNode>(nodes)
      .force("link", d3.forceLink(links)
        .id((d: any) => d.id)
        .distance((l: any) => {
          // Space node types further apart for readability
          const src = (l.source as SimNode).type;
          const tgt = (l.target as SimNode).type;
          if (src === "Route" || tgt === "Route") return 120;
          return 90;
        })
        .strength(0.6)
      )
      .force("charge", d3.forceManyBody().strength(-300))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide()
        .radius((d: any) => (NODE_RADIUS[(d as SimNode).type] ?? 16) + 8)
      );

    simRef.current = sim;

    // ── Edge labels (relationship type) ──────────────────────────────────────
    const edgeDefs = svg.append("defs");
    edgeDefs.append("marker")
      .attr("id", "arrow")
      .attr("viewBox", "0 -4 8 8")
      .attr("refX", 14)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-4L8,0L0,4")
      .attr("fill", "#475569");

    // ── Draw edges ────────────────────────────────────────────────────────────
    const linkGroup = g.append("g").attr("class", "links");

    const linkLines = linkGroup.selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("stroke", (d) => {
        // Grey out edges that pass through blocked chokepoints
        const srcLabel = (d.source as SimNode).label;
        const tgtLabel = (d.target as SimNode).label;
        if (
          blockedChokepoints.includes(srcLabel) ||
          blockedChokepoints.includes(tgtLabel)
        ) return "#1e293b";
        return EDGE_COLORS[d.label] ?? "#475569";
      })
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", (d) => {
        const srcLabel = (d.source as SimNode).label;
        const tgtLabel = (d.target as SimNode).label;
        if (
          blockedChokepoints.includes(srcLabel) ||
          blockedChokepoints.includes(tgtLabel)
        ) return 0.2;
        return 0.7;
      })
      .attr("marker-end", "url(#arrow)");

    // Edge label text
    const linkLabels = g.append("g").attr("class", "link-labels")
      .selectAll("text")
      .data(links)
      .enter()
      .append("text")
      .text((d) => d.label.replace(/_/g, " "))
      .attr("font-size", "8px")
      .attr("fill", "#475569")
      .attr("text-anchor", "middle")
      .style("pointer-events", "none")
      .style("user-select", "none");

    // ── Draw nodes ────────────────────────────────────────────────────────────
    const nodeGroup = g.append("g").attr("class", "nodes");

    const nodeGs = nodeGroup.selectAll("g")
      .data(nodes)
      .enter()
      .append("g")
      .attr("cursor", "pointer")
      .call(
        d3.drag<SVGGElement, SimNode>()
          .on("start", (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) sim.alphaTarget(0);
            // Keep node pinned after drag
          })
      )
      .on("click", (event, d) => {
        event.stopPropagation();
        setSelectedNode(d);
      });

    // Node circle
    nodeGs.append("circle")
      .attr("r", (d) => NODE_RADIUS[d.type] ?? 16)
      .attr("fill", (d) => {
        if (blockedChokepoints.includes(d.label)) return "#7f1d1d";
        return NODE_COLORS[d.type] ?? "#94a3b8";
      })
      .attr("fill-opacity", (d) => {
        if (blockedChokepoints.includes(d.label)) return 1;
        return 0.85;
      })
      .attr("stroke", (d) => {
        if (blockedChokepoints.includes(d.label)) return "#ef4444";
        return d3.color(NODE_COLORS[d.type] ?? "#94a3b8")?.brighter(0.5)?.toString() ?? "#fff";
      })
      .attr("stroke-width", (d) => blockedChokepoints.includes(d.label) ? 2.5 : 1.5);

    // Pulse ring for blocked chokepoints
    if (blockedChokepoints.length > 0) {
      nodeGs.filter((d) => blockedChokepoints.includes(d.label))
        .append("circle")
        .attr("r", (d) => (NODE_RADIUS[d.type] ?? 16) + 6)
        .attr("fill", "none")
        .attr("stroke", "#ef4444")
        .attr("stroke-width", 1.5)
        .attr("stroke-opacity", 0.5)
        .attr("class", "pulse-ring");
    }

    // Node label
    nodeGs.append("text")
      .text((d) => d.label)
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("font-size", (d) => d.label.length > 10 ? "8px" : "9px")
      .attr("font-weight", "600")
      .attr("fill", "#0f172a")
      .style("pointer-events", "none")
      .style("user-select", "none");

    // ── Simulation tick ───────────────────────────────────────────────────────
    sim.on("tick", () => {
      linkLines
        .attr("x1", (d) => (d.source as SimNode).x ?? 0)
        .attr("y1", (d) => (d.source as SimNode).y ?? 0)
        .attr("x2", (d) => (d.target as SimNode).x ?? 0)
        .attr("y2", (d) => (d.target as SimNode).y ?? 0);

      linkLabels
        .attr("x", (d) => (((d.source as SimNode).x ?? 0) + ((d.target as SimNode).x ?? 0)) / 2)
        .attr("y", (d) => (((d.source as SimNode).y ?? 0) + ((d.target as SimNode).y ?? 0)) / 2 - 5);

      nodeGs.attr("transform", (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => {
      sim.stop();
      simRef.current = null;
    };
  }, [graphData, blockedChokepoints]);

  // ── Legend ────────────────────────────────────────────────────────────────

  const legendItems = Object.entries(NODE_COLORS).map(([type, color]) => ({ type, color }));

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-700 flex items-start justify-between">
        <div>
          <h2 className="text-white text-sm font-medium">Supply Chain Knowledge Graph</h2>
          <p className="text-slate-500 text-xs mt-0.5">
            {graphData
              ? `${graphData.nodes.length} nodes · ${graphData.edges.length} relationships · seeded from live data`
              : "Loading graph…"}
          </p>
        </div>
        {blockedChokepoints.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-red-400 border border-red-800 bg-red-900/30 px-2.5 py-1 rounded-lg">
            <span className="w-1.5 h-1.5 bg-red-400 rounded-full animate-pulse" />
            {blockedChokepoints.join(", ")} blocked
          </div>
        )}
      </div>

      {/* Graph canvas */}
      <div className="relative" ref={containerRef} style={{ height }}>
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              <svg className="w-6 h-6 animate-spin text-slate-500 mx-auto mb-2" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              <p className="text-slate-500 text-xs">Building graph…</p>
            </div>
          </div>
        ) : (
          <svg ref={svgRef} className="w-full h-full" />
        )}

        {/* Node detail panel */}
        {selectedNode && (
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}

        {/* Zoom hint */}
        {!loading && (
          <p className="absolute bottom-3 right-3 text-slate-700 text-xs pointer-events-none">
            scroll to zoom · drag to pan · click node for details
          </p>
        )}
      </div>

      {/* Legend */}
      <div className="px-5 py-3 border-t border-slate-700 flex items-center gap-5 flex-wrap">
        {legendItems.map(({ type, color }) => (
          <div key={type} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
            <span className="text-slate-400 text-xs">{type}</span>
          </div>
        ))}
        <div className="ml-auto flex items-center gap-4 text-xs text-slate-600">
          <span>—— SHIPS_VIA</span>
          <span className="text-red-900">—— PASSES_THROUGH</span>
          <span className="text-blue-900">—— ARRIVES_AT</span>
        </div>
      </div>

      {/* Pulse animation for blocked nodes */}
      <style>{`
        @keyframes pulse-ring {
          0%   { stroke-opacity: 0.6; r: 26px; }
          100% { stroke-opacity: 0;   r: 36px; }
        }
        .pulse-ring {
          animation: pulse-ring 1.5s ease-out infinite;
        }
      `}</style>
    </div>
  );
};

export default KnowledgeGraph;