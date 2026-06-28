/**
 * ShippingMap.tsx
 * Day 3 deliverable — Person C (Frontend)
 *
 * Static Leaflet map with:
 *   - 5 Indian port markers (click-to-show detail tooltip)
 *   - GeoJSON shipping lane overlays (Hormuz, Red Sea, Suez, Cape, Gulf of Aden)
 *   - Corridor risk colour-coding wired to CorridorRiskState (live data ready)
 *
 * Day 7 upgrade path:
 *   - Pass `vessels` prop (Vessel[]) → renders AISHub vessel markers automatically
 *   - Pass live `riskState` from WebSocket → lane colours update reactively
 *   - No structural changes needed; just feed the props.
 */

import React, { useEffect, useRef, useState } from "react";
import L, { Map, GeoJSON as LeafletGeoJSON } from "leaflet";
import "leaflet/dist/leaflet.css";
import { CorridorRiskState, Vessel } from "../types";

// ─── Fix Leaflet's broken default marker icon paths in CRA/webpack ───────────
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

// ─── Types ────────────────────────────────────────────────────────────────────

export interface IndianPort {
  id: string;
  name: string;
  city: string;
  lat: number;
  lng: number;
  capacity_mbd: number;        // crude throughput in million barrels/day
  primary_grades: string[];    // crude grades this refinery accepts
  operator: string;
  corridors: Array<keyof CorridorRiskState["corridors"]>;  // corridors it uses
}

interface ShippingMapProps {
  /** Live or mock corridor risk state — drives lane colour coding */
  riskState?: CorridorRiskState | null;
  /** Day 7: pass AISHub vessel list to render vessel markers */
  vessels?: Vessel[];
  /** Tailwind / inline height override — defaults to "500px" */
  height?: string;
}

// ─── Data: Five Indian Ports ──────────────────────────────────────────────────

export const INDIAN_PORTS: IndianPort[] = [
  {
    id: "jamnagar",
    name: "Jamnagar Complex",
    city: "Jamnagar, Gujarat",
    lat: 22.4707,
    lng: 70.0577,
    capacity_mbd: 1.24,
    primary_grades: ["Arab Light", "Murban", "Urals", "Iranian Light"],
    operator: "Reliance Industries",
    corridors: ["Hormuz"],
  },
  {
    id: "vadinar",
    name: "Vadinar (Nayara Energy)",
    city: "Vadinar, Gujarat",
    lat: 22.4604,
    lng: 69.7325,
    capacity_mbd: 0.4,
    primary_grades: ["Arab Light", "Urals", "Basra Light"],
    operator: "Nayara Energy (Rosneft JV)",
    corridors: ["Hormuz"],
  },
  {
    id: "mumbai_hpcl",
    name: "Mumbai Refinery (HPCL)",
    city: "Mahul, Mumbai",
    lat: 19.0438,
    lng: 72.9169,
    capacity_mbd: 0.17,
    primary_grades: ["Arab Light", "Basra Light"],
    operator: "HPCL",
    corridors: ["Hormuz", "Red_Sea"],
  },
  {
    id: "kochi_bpcl",
    name: "Kochi Refinery (BPCL)",
    city: "Kochi, Kerala",
    lat: 9.9312,
    lng: 76.2673,
    capacity_mbd: 0.31,
    primary_grades: ["Arab Light", "Murban"],
    operator: "BPCL",
    corridors: ["Hormuz", "Red_Sea", "Cape"],
  },
  {
    id: "mangalore_mrpl",
    name: "Mangalore Refinery (MRPL)",
    city: "Mangalore, Karnataka",
    lat: 12.9141,
    lng: 74.8560,
    capacity_mbd: 0.3,
    primary_grades: ["Basra Light", "Arab Light", "Murals"],
    operator: "MRPL (ONGC subsidiary)",
    corridors: ["Hormuz", "Red_Sea"],
  },
];

// ─── Corridor colour logic ────────────────────────────────────────────────────

const CORRIDOR_COLORS: Record<string, { low: string; mid: string; high: string }> = {
  Hormuz:  { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Red_Sea: { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Suez:    { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Cape:    { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Unknown: { low: "#94a3b8", mid: "#94a3b8", high: "#94a3b8" },
};

function corridorColour(
  corridorName: string,
  riskState?: CorridorRiskState | null
): string {
  if (!riskState) return "#60a5fa"; // default blue when no data
  const risk =
    riskState.corridors[corridorName as keyof CorridorRiskState["corridors"]] ?? 0;
  const palette = CORRIDOR_COLORS[corridorName] ?? CORRIDOR_COLORS.Unknown;
  if (risk > 0.65) return palette.high;
  if (risk > 0.30) return palette.mid;
  return palette.low;
}

// ─── Custom SVG port icon ─────────────────────────────────────────────────────

function createPortIcon(capacity: number): L.DivIcon {
  // Scale icon slightly by capacity
  const size = capacity >= 0.8 ? 18 : capacity >= 0.3 ? 14 : 11;
  return L.divIcon({
    className: "",
    html: `
      <div style="
        width:${size * 2}px; height:${size * 2}px;
        background:#1e40af;
        border:2.5px solid #60a5fa;
        border-radius:50%;
        display:flex; align-items:center; justify-content:center;
        box-shadow: 0 0 0 3px rgba(96,165,250,0.25);
      ">
        <svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="white">
          <path d="M12 2L8 8H4L2 10l10 12L22 10l-2-2h-4L12 2z" opacity="0.9"/>
        </svg>
      </div>`,
    iconSize: [size * 2, size * 2],
    iconAnchor: [size, size],
    popupAnchor: [0, -(size + 4)],
  });
}

// ─── Component ────────────────────────────────────────────────────────────────

const ShippingMap: React.FC<ShippingMapProps> = ({
  riskState,
  vessels = [],
  height = "500px",
}) => {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const geoJsonLayerRef = useRef<LeafletGeoJSON | null>(null);
  const [selectedPort, setSelectedPort] = useState<IndianPort | null>(null);

  // ── Initialise map (runs once) ──────────────────────────────────────────────
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = L.map(mapContainerRef.current, {
      center: [20, 65],       // centred on Arabian Sea / Indian Ocean
      zoom: 4,
      minZoom: 2,
      maxZoom: 10,
      zoomControl: true,
      attributionControl: true,
    });

    // Dark tile layer that matches the dashboard's slate theme
    L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> contributors &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: "abcd",
        maxZoom: 19,
      }
    ).addTo(map);

    mapRef.current = map;

    // Add port markers
    INDIAN_PORTS.forEach((port) => {
      const marker = L.marker([port.lat, port.lng], {
        icon: createPortIcon(port.capacity_mbd),
        title: port.name,
      }).addTo(map);

      marker.on("click", () => {
        setSelectedPort(port);
      });

      // Minimal tooltip on hover (full detail panel is in the sidebar card)
      marker.bindTooltip(
        `<div style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:6px 10px;border-radius:6px;font-size:12px;line-height:1.5">
          <strong style="color:#60a5fa">${port.name}</strong><br/>
          ${port.city}<br/>
          <span style="color:#94a3b8">Capacity: ${port.capacity_mbd} Mb/d</span>
        </div>`,
        { permanent: false, direction: "top", opacity: 1, className: "leaflet-tooltip-custom" }
      );
    });

    // Load and render GeoJSON shipping lanes
    fetch("/geojson/shipping_lanes.json")
      .then((r) => r.json())
      .then((geojson) => {
        if (!mapRef.current) return;

        const layer = L.geoJSON(geojson, {
          style: (feature) => {
            const corridor = feature?.properties?.corridor ?? "Unknown";
            return {
              color: corridorColour(corridor, riskState),
              weight: corridor === "Cape" ? 2 : 3,
              opacity: 0.8,
              dashArray: corridor === "Cape" ? "8 5" : undefined,
            };
          },
          onEachFeature: (feature, layer) => {
            const p = feature.properties;
            layer.bindTooltip(
              `<div style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:6px 10px;border-radius:6px;font-size:12px">
                <strong style="color:#60a5fa">${p.name}</strong><br/>
                ${p.description}<br/>
                <span style="color:#94a3b8">Flow: ~${p.daily_barrels_mbd} Mb/d</span>
              </div>`,
              { sticky: true, opacity: 1, className: "leaflet-tooltip-custom" }
            );
          },
        }).addTo(mapRef.current);

        geoJsonLayerRef.current = layer;
      })
      .catch((err) => {
        console.error("Failed to load shipping lanes GeoJSON:", err);
      });

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — map init runs once

  // ── Reactively update lane colours when riskState changes ──────────────────
  // Day 7: this same block will re-run every time the WebSocket pushes RISK_STATE_UPDATED
  useEffect(() => {
    if (!geoJsonLayerRef.current || !riskState) return;
    geoJsonLayerRef.current.setStyle((feature) => {
      const corridor = feature?.properties?.corridor ?? "Unknown";
      return {
        color: corridorColour(corridor, riskState),
        weight: corridor === "Cape" ? 2 : 3,
        opacity: 0.8,
        dashArray: corridor === "Cape" ? "8 5" : undefined,
      };
    });
  }, [riskState]);

  // ── Day 7: render AIS vessel markers ──────────────────────────────────────
  // When `vessels` prop is fed live data, this effect drops markers onto the map.
  const vesselLayerGroupRef = useRef<L.LayerGroup | null>(null);
  useEffect(() => {
    if (!mapRef.current) return;
    if (vesselLayerGroupRef.current) {
      vesselLayerGroupRef.current.clearLayers();
    } else {
      vesselLayerGroupRef.current = L.layerGroup().addTo(mapRef.current);
    }

    vessels.forEach((v) => {
      const icon = L.divIcon({
        className: "",
        html: `<div style="
          width:10px;height:10px;
          background:#f59e0b;border:1.5px solid #fde68a;
          border-radius:2px;
          transform:rotate(${v.heading_degrees}deg);
        "></div>`,
        iconSize: [10, 10],
        iconAnchor: [5, 5],
      });
      const m = L.marker([v.latitude, v.longitude], { icon });
      m.bindTooltip(
        `<div style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:6px 10px;border-radius:6px;font-size:11px">
          <strong style="color:#fbbf24">${v.name}</strong><br/>
          Type: ${v.vessel_type} | Speed: ${v.speed_knots} kn
        </div>`,
        { sticky: true, opacity: 1, className: "leaflet-tooltip-custom" }
      );
      vesselLayerGroupRef.current?.addLayer(m);
    });
  }, [vessels]);

  // ─── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="relative w-full" style={{ height }}>
      {/* Map container */}
      <div ref={mapContainerRef} className="w-full h-full rounded-xl overflow-hidden" />

      {/* Port detail panel — appears when a port marker is clicked */}
      {selectedPort && (
        <div
          className="absolute top-3 right-3 z-[1000] w-72 bg-slate-900 border border-slate-700 rounded-xl p-4 shadow-xl"
          style={{ pointerEvents: "all" }}
        >
          {/* Header */}
          <div className="flex items-start justify-between mb-3">
            <div>
              <p className="text-white font-medium text-sm leading-tight">
                {selectedPort.name}
              </p>
              <p className="text-slate-400 text-xs mt-0.5">{selectedPort.city}</p>
            </div>
            <button
              onClick={() => setSelectedPort(null)}
              className="text-slate-500 hover:text-white text-lg leading-none ml-2 mt-0.5"
              aria-label="Close"
            >
              ×
            </button>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-2 mb-3">
            <div className="bg-slate-800 rounded-lg p-2">
              <p className="text-slate-500 text-xs">Capacity</p>
              <p className="text-blue-400 font-medium text-sm">
                {selectedPort.capacity_mbd} Mb/d
              </p>
            </div>
            <div className="bg-slate-800 rounded-lg p-2">
              <p className="text-slate-500 text-xs">Operator</p>
              <p className="text-slate-300 font-medium text-xs leading-tight">
                {selectedPort.operator}
              </p>
            </div>
          </div>

          {/* Crude grades */}
          <div className="mb-3">
            <p className="text-slate-500 text-xs mb-1.5">Accepted Crude Grades</p>
            <div className="flex flex-wrap gap-1">
              {selectedPort.primary_grades.map((g) => (
                <span
                  key={g}
                  className="text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full border border-slate-700"
                >
                  {g}
                </span>
              ))}
            </div>
          </div>

          {/* Corridors with live risk colour */}
          <div>
            <p className="text-slate-500 text-xs mb-1.5">Supply Corridors</p>
            <div className="flex flex-wrap gap-1">
              {selectedPort.corridors.map((c) => {
                const risk = riskState?.corridors[c] ?? null;
                const colorClass =
                  risk === null
                    ? "text-slate-400 border-slate-700"
                    : risk > 0.65
                    ? "text-red-400 border-red-800"
                    : risk > 0.3
                    ? "text-amber-400 border-amber-800"
                    : "text-green-400 border-green-800";
                return (
                  <span
                    key={c}
                    className={`text-xs px-2 py-0.5 rounded-full border bg-slate-800 ${colorClass}`}
                  >
                    {c.replace("_", " ")}
                    {risk !== null && ` ${(risk * 100).toFixed(0)}%`}
                  </span>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-3 left-3 z-[1000] bg-slate-900/90 border border-slate-700 rounded-lg p-3 text-xs">
        <p className="text-slate-400 font-medium mb-2">Corridor Risk</p>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="w-6 h-0.5 bg-green-500" />
            <span className="text-slate-400">Normal (&lt;30%)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-0.5 bg-amber-500" />
            <span className="text-slate-400">Elevated (30–65%)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-0.5 bg-red-500" />
            <span className="text-slate-400">Critical (&gt;65%)</span>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <div className="w-6 h-0.5 bg-blue-400" style={{ borderTop: "2px dashed #60a5fa", height: 0 }} />
            <span className="text-slate-400">Cape (fallback)</span>
          </div>
          {vessels.length > 0 && (
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 bg-amber-400 rounded-sm" />
              <span className="text-slate-400">AIS Vessel</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ShippingMap;