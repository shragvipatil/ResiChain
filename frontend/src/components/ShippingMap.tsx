import React, { useEffect, useRef, useState } from "react";
import L, { Map, GeoJSON as LeafletGeoJSON } from "leaflet";
import "leaflet/dist/leaflet.css";
import { CorridorRiskState, Vessel } from "../types";

import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

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
  riskState?: CorridorRiskState | null;
  vessels?: Vessel[];
  height?: string;
}

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
    lng: 74.856,
    capacity_mbd: 0.3,
    primary_grades: ["Basra Light", "Arab Light", "Murban"],
    operator: "MRPL (ONGC subsidiary)",
    corridors: ["Hormuz", "Red_Sea"],
  },
];

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
  if (!riskState) return "#60a5fa";
  const detail = riskState.corridors[corridorName as keyof CorridorRiskState["corridors"]];
  const risk = detail?.risk_score ?? 0;
  const palette = CORRIDOR_COLORS[corridorName] ?? CORRIDOR_COLORS.Unknown;
  if (risk > 0.65) return palette.high;
  if (risk > 0.3)  return palette.mid;
  return palette.low;
}

function createPortIcon(capacity: number): L.DivIcon {
  const size = capacity >= 0.8 ? 18 : capacity >= 0.3 ? 14 : 11;
  return L.divIcon({
    className: "",
    html: `
      <div style="
        width:${size * 2}px;height:${size * 2}px;
        background:#1e40af;
        border:2.5px solid #60a5fa;
        border-radius:50%;
        display:flex;align-items:center;justify-content:center;
        box-shadow:0 0 0 3px rgba(96,165,250,0.25);
      ">
        <svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="white">
          <path d="M12 2L8 8H4L2 10l10 12L22 10l-2-2h-4L12 2z"/>
        </svg>
      </div>
    `,
    iconSize: [size * 2, size * 2],
    iconAnchor: [size, size],
  });
}

const ShippingMap: React.FC<ShippingMapProps> = ({
  riskState,
  vessels = [],
  height = "500px",
}) => {
  const mapContainerRef      = useRef<HTMLDivElement>(null);
  const mapRef               = useRef<Map | null>(null);
  const geoJsonLayerRef      = useRef<LeafletGeoJSON | null>(null);
  const vesselLayerGroupRef  = useRef<L.LayerGroup | null>(null);

  const [selectedPort, setSelectedPort] = useState<IndianPort | null>(null);

  // ── Map initialisation (runs once) ──────────────────────────────────────────
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = L.map(mapContainerRef.current, {
      center: [20, 65],
      zoom: 4,
      minZoom: 2,
      maxZoom: 10,
    });

    L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
    ).addTo(map);

    mapRef.current = map;

    // Create vessel layer group immediately — must exist before vessel effect runs
    vesselLayerGroupRef.current = L.layerGroup().addTo(map);

    // Port markers
    INDIAN_PORTS.forEach((port) => {
      const marker = L.marker([port.lat, port.lng], {
        icon: createPortIcon(port.capacity_mbd),
      }).addTo(map);

      marker.on("click", () => setSelectedPort(port));
      marker.bindTooltip(
        `<div style="background:#0f172a;color:#e2e8f0;padding:6px 10px;border-radius:6px">
          <strong>${port.name}</strong><br/>
          ${port.city}
        </div>`
      );
    });

    // GeoJSON shipping lanes — guarded so async callback can't fire after unmount
    let destroyed = false;

    fetch("/geojson/shipping_lanes.json")
      .then((r) => r.json())
      .then((geojson) => {
        if (destroyed) return;
        const layer = L.geoJSON(geojson, {
          style: (feature) => {
            const corridor = feature?.properties?.corridor ?? "Unknown";
            return {
              color: corridorColour(corridor, riskState),
              weight: corridor === "Cape" ? 2 : 3,
              opacity: 0.8,
            };
          },
        }).addTo(map);
        geoJsonLayerRef.current = layer;
      })
      .catch(() => {
        // GeoJSON file missing — map still works without lanes
      });

    return () => {
      destroyed = true;
      map.remove();
      mapRef.current = null;
      vesselLayerGroupRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Risk colour update (runs when riskState changes) ────────────────────────
  useEffect(() => {
    if (!geoJsonLayerRef.current || !riskState) return;
    geoJsonLayerRef.current.setStyle((feature) => {
      const corridor = feature?.properties?.corridor ?? "Unknown";
      return {
        color: corridorColour(corridor, riskState),
        weight: corridor === "Cape" ? 2 : 3,
        opacity: 0.8,
      };
    });
  }, [riskState]);

  // ── Vessel markers (runs when vessels array changes) ────────────────────────
  useEffect(() => {
    if (!mapRef.current || !vesselLayerGroupRef.current) return;

    vesselLayerGroupRef.current.clearLayers();

    vessels.forEach((v) => {
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:10px;height:10px;background:#f59e0b;border-radius:2px"></div>`,
      });
      const marker = L.marker([v.latitude, v.longitude], { icon });
      vesselLayerGroupRef.current?.addLayer(marker);
    });
  }, [vessels]);

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={mapContainerRef} className="w-full h-full rounded-xl" />

      {selectedPort && (
        <div className="absolute top-3 right-3 z-[1000] bg-slate-900 p-4 rounded-xl border border-slate-700">
          <button
            onClick={() => setSelectedPort(null)}
            className="absolute top-2 right-2 text-slate-500 hover:text-white text-xs"
          >
            ✕
          </button>
          <p className="text-white font-medium">{selectedPort.name}</p>
          <p className="text-slate-400 text-xs">{selectedPort.city}</p>
          <p className="text-slate-500 text-xs mt-1">{selectedPort.operator}</p>
          <div className="mt-2 text-xs text-slate-400">
            <p className="text-slate-500 mb-1">Corridors</p>
            {selectedPort.corridors.map((c) => {
              const risk = riskState?.corridors[c]?.risk_score ?? null;
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
                  className={`inline-block text-xs px-2 py-0.5 rounded-full border bg-slate-800 mr-1 ${colorClass}`}
                >
                  {c.replace("_", " ")}
                  {risk !== null && ` ${(risk * 100).toFixed(0)}%`}
                </span>
              );
            })}
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
          {vessels.length > 0 && (
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 bg-amber-400 rounded-sm" />
              <span className="text-slate-400">Vessel</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ShippingMap;