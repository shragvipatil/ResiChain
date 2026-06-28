/**
 * ShippingMap.tsx — updated Day 7
 *
 * New in Day 7:
 *   1. Vessel markers — amber squares, update every 5 min from getVessels()
 *      Rotated by heading_degrees, tooltip shows name/type/speed.
 *   2. Risk overlay — already existed; now confirmed reactive to WebSocket riskState.
 *   3. Cape route animation — hidden dashed polyline reveals itself over 3 seconds
 *      when `animateCapeRoute` prop becomes true (set by compoundDisruptionDetected
 *      in AppContext, triggered by COMPOUND_DISRUPTION_DETECTED WebSocket event).
 *      Per CLAUDE.md: "routes disappear sequentially, give this moment silence."
 *      The Cape line draws itself — no narration needed.
 *
 * Day 9 upgrade path: zero changes needed here.
 *   - Vessel data will come from real AISHub via getVessels() — same prop.
 *   - compoundDisruptionDetected fires from real LangGraph WS event — same prop.
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import L, { Map, GeoJSON as LeafletGeoJSON } from "leaflet";
import "leaflet/dist/leaflet.css";
import { CorridorRiskState, Vessel } from "../types";

import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon   from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl:       markerIcon,
  shadowUrl:     markerShadow,
});

// ── Types ─────────────────────────────────────────────────────────────────────

export interface IndianPort {
  id: string;
  name: string;
  city: string;
  lat: number;
  lng: number;
  capacity_mbd: number;
  primary_grades: string[];
  operator: string;
  corridors: Array<keyof CorridorRiskState["corridors"]>;
}

interface ShippingMapProps {
  riskState?:        CorridorRiskState | null;
  vessels?:          Vessel[];
  animateCapeRoute?: boolean;   // true = play the 3-second Cape animation
  height?:           string;
}

// ── Port data ─────────────────────────────────────────────────────────────────

export const INDIAN_PORTS: IndianPort[] = [
  { id: "jamnagar",     name: "Jamnagar Complex",        city: "Jamnagar, Gujarat",   lat: 22.4707, lng: 70.0577, capacity_mbd: 1.24, primary_grades: ["Arab Light","Murban","Urals","Iranian Light"], operator: "Reliance Industries",    corridors: ["Hormuz"] },
  { id: "vadinar",      name: "Vadinar (Nayara Energy)", city: "Vadinar, Gujarat",     lat: 22.4604, lng: 69.7325, capacity_mbd: 0.40, primary_grades: ["Arab Light","Urals","Basra Light"],             operator: "Nayara Energy (Rosneft JV)", corridors: ["Hormuz"] },
  { id: "mumbai_hpcl",  name: "Mumbai Refinery (HPCL)",  city: "Mahul, Mumbai",        lat: 19.0438, lng: 72.9169, capacity_mbd: 0.17, primary_grades: ["Arab Light","Basra Light"],                     operator: "HPCL",                  corridors: ["Hormuz","Red_Sea"] },
  { id: "kochi_bpcl",   name: "Kochi Refinery (BPCL)",   city: "Kochi, Kerala",        lat: 9.9312,  lng: 76.2673, capacity_mbd: 0.31, primary_grades: ["Arab Light","Murban"],                          operator: "BPCL",                  corridors: ["Hormuz","Red_Sea","Cape"] },
  { id: "mangalore_mrpl",name:"Mangalore Refinery (MRPL)",city: "Mangalore, Karnataka",lat: 12.9141, lng: 74.8560, capacity_mbd: 0.30, primary_grades: ["Basra Light","Arab Light","Murban"],             operator: "MRPL (ONGC subsidiary)", corridors: ["Hormuz","Red_Sea"] },
];

// ── Cape route waypoints (Gulf of Oman → Cape of Good Hope → Indian west coast) ──
// Per CLAUDE.md: "the only surviving route glowing" — full path shown end-to-end
const CAPE_ROUTE_LATLNGS: [number, number][] = [
  [23.5,  58.5],  // Gulf of Oman departure
  [18.0,  57.0],  // Arabian Sea south
  [12.0,  52.0],  // Gulf of Aden south
  [5.0,   48.0],  // Indian Ocean north
  [-5.0,  42.0],
  [-15.0, 38.0],
  [-25.0, 33.0],
  [-34.5, 19.0],  // Cape of Good Hope
  [-30.0, 15.0],
  [-20.0, 12.0],
  [-10.0, 15.0],
  [0.0,   20.0],
  [5.0,   30.0],
  [10.0,  40.0],
  [9.9,   76.3],  // Kochi arrival
];

// ── Corridor colour helper ────────────────────────────────────────────────────

const CORRIDOR_COLORS: Record<string, { low: string; mid: string; high: string }> = {
  Hormuz:  { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Red_Sea: { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Suez:    { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Cape:    { low: "#22c55e", mid: "#f59e0b", high: "#ef4444" },
  Unknown: { low: "#94a3b8", mid: "#94a3b8", high: "#94a3b8" },
};

function corridorColour(corridorName: string, riskState?: CorridorRiskState | null): string {
  if (!riskState) return "#60a5fa";
  const detail = riskState.corridors[corridorName as keyof CorridorRiskState["corridors"]];
  const risk = detail?.risk_score ?? 0;
  const palette = CORRIDOR_COLORS[corridorName] ?? CORRIDOR_COLORS.Unknown;
  if (risk > 0.65) return palette.high;
  if (risk > 0.30) return palette.mid;
  return palette.low;
}

// ── Port icon ─────────────────────────────────────────────────────────────────

function createPortIcon(capacity: number): L.DivIcon {
  const size = capacity >= 0.8 ? 18 : capacity >= 0.3 ? 14 : 11;
  return L.divIcon({
    className: "",
    html: `<div style="width:${size*2}px;height:${size*2}px;background:#1e40af;border:2.5px solid #60a5fa;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 3px rgba(96,165,250,0.25)">
      <svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="white"><path d="M12 2L8 8H4L2 10l10 12L22 10l-2-2h-4L12 2z"/></svg>
    </div>`,
    iconSize:   [size * 2, size * 2],
    iconAnchor: [size, size],
    popupAnchor:[0, -(size + 4)],
  });
}

// ── Vessel icon (rotated amber square) ───────────────────────────────────────

function createVesselIcon(heading: number): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:10px;height:10px;
      background:#f59e0b;
      border:1.5px solid #fde68a;
      border-radius:2px;
      transform:rotate(${heading}deg);
      box-shadow:0 0 4px rgba(245,158,11,0.5);
    "></div>`,
    iconSize:   [10, 10],
    iconAnchor: [5, 5],
  });
}

// ── Component ─────────────────────────────────────────────────────────────────

const ShippingMap: React.FC<ShippingMapProps> = ({
  riskState,
  vessels = [],
  animateCapeRoute = false,
  height = "500px",
}) => {
  const mapContainerRef     = useRef<HTMLDivElement>(null);
  const mapRef              = useRef<Map | null>(null);
  const geoJsonLayerRef     = useRef<LeafletGeoJSON | null>(null);
  const vesselLayerGroupRef = useRef<L.LayerGroup | null>(null);
  const capePolylineRef     = useRef<L.Polyline | null>(null);
  const capeAnimFrameRef    = useRef<number | null>(null);

  const [selectedPort, setSelectedPort] = useState<IndianPort | null>(null);

  // ── Map init (once) ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = L.map(mapContainerRef.current, {
      center: [20, 65], zoom: 4, minZoom: 2, maxZoom: 10,
    });

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; OSM contributors &copy; CARTO',
      subdomains: "abcd", maxZoom: 19,
    }).addTo(map);

    mapRef.current = map;
    vesselLayerGroupRef.current = L.layerGroup().addTo(map);

    // Port markers
    INDIAN_PORTS.forEach((port) => {
      const marker = L.marker([port.lat, port.lng], { icon: createPortIcon(port.capacity_mbd) }).addTo(map);
      marker.on("click", () => setSelectedPort(port));
      marker.bindTooltip(
        `<div style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:6px 10px;border-radius:6px;font-size:12px">
          <strong style="color:#60a5fa">${port.name}</strong><br/>${port.city}
        </div>`,
        { permanent: false, direction: "top", opacity: 1 }
      );
    });

    // GeoJSON shipping lanes
    let destroyed = false;
    fetch("/geojson/shipping_lanes.json")
      .then((r) => r.json())
      .then((geojson) => {
        if (destroyed || !mapRef.current) return;
        const layer = L.geoJSON(geojson, {
          style: (feature) => {
            const corridor = feature?.properties?.corridor ?? "Unknown";
            return {
              color:    corridorColour(corridor, riskState),
              weight:   corridor === "Cape" ? 2 : 3,
              opacity:  0.8,
              dashArray: corridor === "Cape" ? "8 5" : undefined,
            };
          },
          onEachFeature: (feature, layer) => {
            const p = feature.properties;
            layer.bindTooltip(
              `<div style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:6px 10px;border-radius:6px;font-size:12px">
                <strong style="color:#60a5fa">${p.name}</strong><br/>
                ${p.description}<br/>
                <span style="color:#94a3b8">~${p.daily_barrels_mbd} Mb/d</span>
              </div>`,
              { sticky: true, opacity: 1 }
            );
          },
        }).addTo(mapRef.current);
        geoJsonLayerRef.current = layer;
      })
      .catch(() => {});

    // Cape route polyline — hidden by default, 0 points until animation plays
    const capeLine = L.polyline([], {
      color:     "#22d3ee",   // cyan — distinct from normal green lanes
      weight:    3,
      opacity:   0.9,
      dashArray: "10 6",
    }).addTo(map);
    capePolylineRef.current = capeLine;

    return () => {
      destroyed = true;
      if (capeAnimFrameRef.current) cancelAnimationFrame(capeAnimFrameRef.current);
      map.remove();
      mapRef.current              = null;
      vesselLayerGroupRef.current = null;
      capePolylineRef.current     = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Reactive risk colour update ───────────────────────────────────────────
  useEffect(() => {
    if (!geoJsonLayerRef.current || !riskState) return;
    geoJsonLayerRef.current.setStyle((feature) => {
      const corridor = feature?.properties?.corridor ?? "Unknown";
      return {
        color:    corridorColour(corridor, riskState),
        weight:   corridor === "Cape" ? 2 : 3,
        opacity:  0.8,
        dashArray: corridor === "Cape" ? "8 5" : undefined,
      };
    });
  }, [riskState]);

  // ── Vessel markers (update on every poll result) ──────────────────────────
  useEffect(() => {
    if (!mapRef.current || !vesselLayerGroupRef.current) return;
    vesselLayerGroupRef.current.clearLayers();

    vessels.forEach((v) => {
      const marker = L.marker([v.latitude, v.longitude], {
        icon:  createVesselIcon(v.heading_degrees),
        title: v.name,
      });
      marker.bindTooltip(
        `<div style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;padding:6px 10px;border-radius:6px;font-size:11px">
          <strong style="color:#fbbf24">${v.name}</strong><br/>
          ${v.vessel_type} · ${v.speed_knots} kn<br/>
          <span style="color:#94a3b8">Hdg ${v.heading_degrees}°</span>
        </div>`,
        { sticky: true, opacity: 1 }
      );
      vesselLayerGroupRef.current?.addLayer(marker);
    });
  }, [vessels]);

  // ── Cape route animation ──────────────────────────────────────────────────
  // Triggered by animateCapeRoute prop (set from compoundDisruptionDetected in context)
  // Per CLAUDE.md: draws itself over 3 seconds sequentially — give it silence.
  const runCapeAnimation = useCallback(() => {
    if (!capePolylineRef.current || !mapRef.current) return;

    const polyline   = capePolylineRef.current;
    const totalPoints = CAPE_ROUTE_LATLNGS.length;
    const durationMs  = 3000;
    const msPerPoint  = durationMs / totalPoints;
    let   pointsShown = 0;

    // Reset to empty
    polyline.setLatLngs([]);

    // Pan map to show the Cape route
    mapRef.current.flyTo([-5, 45], 3, { duration: 1.5, easeLinearity: 0.5 });

    const addNextPoint = () => {
      if (!capePolylineRef.current) return;
      if (pointsShown >= totalPoints) return;

      pointsShown++;
      capePolylineRef.current.setLatLngs(
        CAPE_ROUTE_LATLNGS.slice(0, pointsShown) as L.LatLngExpression[]
      );

      capeAnimFrameRef.current = window.setTimeout(addNextPoint, msPerPoint);
    };

    // Small delay so map pan starts first
    capeAnimFrameRef.current = window.setTimeout(addNextPoint, 800);
  }, []);

  useEffect(() => {
    if (!animateCapeRoute) return;
    runCapeAnimation();
    return () => {
      if (capeAnimFrameRef.current) clearTimeout(capeAnimFrameRef.current);
    };
  }, [animateCapeRoute, runCapeAnimation]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={mapContainerRef} className="w-full h-full rounded-xl overflow-hidden" />

      {/* Port detail panel */}
      {selectedPort && (
        <div className="absolute top-3 right-3 z-[1000] w-72 bg-slate-900 border border-slate-700 rounded-xl p-4 shadow-xl">
          <div className="flex items-start justify-between mb-3">
            <div>
              <p className="text-white font-medium text-sm">{selectedPort.name}</p>
              <p className="text-slate-400 text-xs mt-0.5">{selectedPort.city}</p>
            </div>
            <button onClick={() => setSelectedPort(null)} className="text-slate-500 hover:text-white text-lg ml-2">×</button>
          </div>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <div className="bg-slate-800 rounded-lg p-2">
              <p className="text-slate-500 text-xs">Capacity</p>
              <p className="text-blue-400 font-medium text-sm">{selectedPort.capacity_mbd} Mb/d</p>
            </div>
            <div className="bg-slate-800 rounded-lg p-2">
              <p className="text-slate-500 text-xs">Operator</p>
              <p className="text-slate-300 text-xs leading-tight">{selectedPort.operator}</p>
            </div>
          </div>
          <div className="mb-3">
            <p className="text-slate-500 text-xs mb-1.5">Accepted Grades</p>
            <div className="flex flex-wrap gap-1">
              {selectedPort.primary_grades.map((g) => (
                <span key={g} className="text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full border border-slate-700">{g}</span>
              ))}
            </div>
          </div>
          <div>
            <p className="text-slate-500 text-xs mb-1.5">Corridors</p>
            <div className="flex flex-wrap gap-1">
              {selectedPort.corridors.map((c) => {
                const risk = riskState?.corridors[c]?.risk_score ?? null;
                const col  = risk === null ? "text-slate-400 border-slate-700"
                  : risk > 0.65 ? "text-red-400 border-red-800"
                  : risk > 0.30 ? "text-amber-400 border-amber-800"
                  : "text-green-400 border-green-800";
                return (
                  <span key={c} className={`text-xs px-2 py-0.5 rounded-full border bg-slate-800 ${col}`}>
                    {c.replace("_"," ")}{risk !== null && ` ${(risk*100).toFixed(0)}%`}
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
          <div className="flex items-center gap-2"><div className="w-6 h-0.5 bg-green-500"/><span className="text-slate-400">Normal (&lt;30%)</span></div>
          <div className="flex items-center gap-2"><div className="w-6 h-0.5 bg-amber-500"/><span className="text-slate-400">Elevated (30–65%)</span></div>
          <div className="flex items-center gap-2"><div className="w-6 h-0.5 bg-red-500"  /><span className="text-slate-400">Critical (&gt;65%)</span></div>
          {animateCapeRoute && (
            <div className="flex items-center gap-2"><div className="w-6 h-0.5 bg-cyan-400" style={{borderTop:"2px dashed #22d3ee",height:0}}/><span className="text-cyan-400 font-medium">Cape Route (active)</span></div>
          )}
          {vessels.length > 0 && (
            <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 bg-amber-400 rounded-sm"/><span className="text-slate-400">AIS Vessel ({vessels.length})</span></div>
          )}
        </div>
      </div>

      {/* Cape route banner — shows while animation is active */}
      {animateCapeRoute && (
        <div className="absolute top-3 left-3 z-[1000] bg-cyan-900/80 border border-cyan-700 rounded-lg px-3 py-2">
          <p className="text-cyan-300 text-xs font-medium">⚠ Cape of Good Hope — Only Surviving Route</p>
          <p className="text-cyan-500 text-xs">Hormuz + Red Sea both blocked</p>
        </div>
      )}
    </div>
  );
};

export default ShippingMap;