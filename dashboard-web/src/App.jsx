import { useState, useEffect, useCallback, useRef } from "react";
import { 
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, 
  AreaChart, Area, CartesianGrid, Legend 
} from "recharts";
import { 
  Activity, Users, ShoppingCart, Percent, AlertTriangle, 
  RefreshCw, CheckCircle, HelpCircle, Shield, Clock, TrendingUp,
  MapPin, Eye, Store, Terminal, Layers
} from "lucide-react";

const API_BASE = "http://localhost:8000";

// Zone layouts for the two stores
const ZONE_LAYOUTS = {
  ST1008: {
    MAKEUP_MIRROR:          { x: 5,  y: 12, w: 22, h: 32, label: "Makeup Mirror", category: "Makeup" },
    CENTER_AISLE:           { x: 31, y: 12, w: 18, h: 50, label: "Center Aisle", category: "Navigation" },
    SUMMER_DISPLAY:         { x: 31, y: 68, w: 18, h: 22, label: "Summer Display", category: "Promo" },
    MAKEUP_SHELF_MAIN:      { x: 53, y: 12, w: 20, h: 50, label: "Makeup Main Shelf", category: "Makeup" },
    MAKEUP_SHELF_PREMIUM:   { x: 77, y: 12, w: 18, h: 50, label: "Makeup Premium", category: "Makeup" },
    SKINCARE_SHELF_LEFT:    { x: 5,  y: 48, w: 22, h: 42, label: "Skincare Shelf", category: "Skincare" },
    BILLING_COUNTER:        { x: 53, y: 68, w: 42, h: 22, label: "Billing Counter", category: "Billing" },
  },
  ST1009: {
    MK_GONDOLA_2:           { x: 5,  y: 12, w: 42, h: 32, label: "Makeup Gondola 2", category: "Makeup" },
    MK_GONDOLA_1:           { x: 5,  y: 48, w: 42, h: 42, label: "Makeup Gondola 1", category: "Makeup" },
    MAKEUP_TABLES:          { x: 51, y: 12, w: 44, h: 50, label: "Makeup Tables", category: "Makeup" },
    BILLING_COUNTER:        { x: 51, y: 68, w: 44, h: 22, label: "Billing Counter", category: "Billing" },
  }
};

// Heat score color generator (slate theme with soft glowing orange/red overlays)
function getHeatColor(score) {
  if (score >= 90) return { bg: "rgba(239, 68, 68, 0.8)", border: "border-red-500", glow: "shadow-red-500/50", text: "text-white" };
  if (score >= 70) return { bg: "rgba(249, 115, 22, 0.8)", border: "border-orange-500", glow: "shadow-orange-500/40", text: "text-white" };
  if (score >= 50) return { bg: "rgba(234, 179, 8, 0.7)", border: "border-yellow-500", glow: "shadow-yellow-500/30", text: "text-slate-900" };
  if (score >= 30) return { bg: "rgba(16, 185, 129, 0.6)", border: "border-emerald-500", glow: "shadow-emerald-500/20", text: "text-white" };
  if (score >= 10) return { bg: "rgba(59, 130, 246, 0.4)", border: "border-blue-500", glow: "shadow-blue-500/10", text: "text-blue-100" };
  return { bg: "rgba(71, 85, 105, 0.2)", border: "border-slate-700", glow: "shadow-none", text: "text-slate-400" };
}

function formatDwell(ms) {
  if (!ms) return "0s";
  if (ms < 1000) return `${ms}ms`;
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = Math.round(secs % 60);
  return `${mins}m ${remSecs}s`;
}

export default function App() {
  const [storeId, setStoreId] = useState("ST1008");
  const [heatmap, setHeatmap] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [liveEvents, setLiveEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [wsStatus, setWsStatus] = useState("connecting"); // 'connected' | 'disconnected' | 'connecting'
  const [hoveredZone, setHoveredZone] = useState(null); // { zone, layout }
  const [selectedZone, setSelectedZone] = useState(null);
  
  const wsRef = useRef(null);

  // Fetch initial data
  const fetchAllData = useCallback(async (targetStoreId = storeId) => {
    try {
      setLoading(true);
      setError(null);
      
      const [heatmapRes, metricsRes, funnelRes, anomaliesRes] = await Promise.all([
        fetch(`${API_BASE}/stores/${targetStoreId}/heatmap`),
        fetch(`${API_BASE}/stores/${targetStoreId}/metrics`),
        fetch(`${API_BASE}/stores/${targetStoreId}/funnel`),
        fetch(`${API_BASE}/stores/${targetStoreId}/anomalies`)
      ]);

      if (!heatmapRes.ok || !metricsRes.ok || !funnelRes.ok || !anomaliesRes.ok) {
        throw new Error("One or more backend API endpoints failed to load.");
      }

      setHeatmap(await heatmapRes.json());
      setMetrics(await metricsRes.json());
      setFunnel(await funnelRes.json());
      
      const anomaliesData = await anomaliesRes.json();
      setAnomalies(anomaliesData.anomalies || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [storeId]);

  // Connect to WebSockets
  useEffect(() => {
    // Reset live events list on store change
    setLiveEvents([]);
    
    // Close existing connection if any
    if (wsRef.current) {
      wsRef.current.close();
    }

    setWsStatus("connecting");
    const wsUrl = `ws://${window.location.hostname}:8000/stores/${storeId}/ws`;
    const socket = new WebSocket(wsUrl);
    wsRef.current = socket;

    socket.onopen = () => {
      setWsStatus("connected");
      setError(null);
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "LIVE_EVENT") {
          // Add event to live feed list
          setLiveEvents(prev => [
            {
              id: msg.event.event_id || Math.random().toString(),
              type: msg.event.event_type,
              zone: msg.event.zone_id,
              visitor: msg.event.visitor_id,
              timestamp: new Date().toLocaleTimeString(),
              isStaff: msg.event.is_staff === 1
            },
            ...prev.slice(0, 49) // Keep last 50 events
          ]);
          
          // Instantly refresh store data on new ingestion event (sub-second UI updates)
          fetchAllData(storeId);
        }
      } catch (err) {
        console.error("Error parsing WebSocket message:", err);
      }
    };

    socket.onclose = () => {
      setWsStatus("disconnected");
    };

    socket.onerror = () => {
      setWsStatus("disconnected");
    };

    // Initial load
    fetchAllData(storeId);

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [storeId, fetchAllData]);

  const handleStoreChange = (newStoreId) => {
    setStoreId(newStoreId);
    setSelectedZone(null);
    setHoveredZone(null);
  };

  // Process data for Recharts
  const zones = heatmap?.zones || [];
  const activeLayout = ZONE_LAYOUTS[storeId] || {};
  
  const sortedZones = [...zones].sort((a, b) => b.heat_score - a.heat_score);
  const dwellChartData = sortedZones
    .map(z => ({
      name: activeLayout[z.zone_id]?.label || z.zone_id,
      dwell: Math.round(z.avg_dwell_ms / 1000),
      heat: z.heat_score,
      raw: z
    }))
    .filter(d => activeLayout[d.raw.zone_id] !== undefined);

  const funnelChartData = funnel?.funnel.map(stage => ({
    name: stage.stage.replace("BILLING_", "").replace("ZONE_", ""),
    visitors: stage.visitors,
    "Drop Off": stage.drop_off_pct,
  })) || [];

  const queueAbandonmentRate = metrics ? Math.round(metrics.abandonment_rate * 100) : 0;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-4 lg:p-6 selection:bg-purple-600 selection:text-white">
      
      {/* Header */}
      <header className="flex flex-col md:flex-row md:items-center md:justify-between border-b border-slate-800 pb-4 mb-6 gap-4">
        <div>
          <div className="flex items-center gap-3">
            <Layers className="h-6 w-6 text-purple-500 animate-pulse" />
            <h1 className="text-xl md:text-2xl font-bold tracking-tight bg-gradient-to-r from-purple-400 via-pink-400 to-indigo-400 bg-clip-text text-transparent">
              Store Intelligence Live Panel
            </h1>
          </div>
          <p className="text-xs text-slate-400 mt-1 flex items-center gap-2">
            <MapPin className="h-3 w-3 text-purple-400" />
            {storeId === "ST1008" ? "Brigade Road Store · Store 1" : "Phoenix Marketcity Store · Store 2"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Store Selector */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-0.5 flex gap-1">
            <button
              onClick={() => handleStoreChange("ST1008")}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                storeId === "ST1008" 
                  ? "bg-purple-600 text-white shadow-md shadow-purple-600/20" 
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Store 1 (ST1008)
            </button>
            <button
              onClick={() => handleStoreChange("ST1009")}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                storeId === "ST1009" 
                  ? "bg-purple-600 text-white shadow-md shadow-purple-600/20" 
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Store 2 (ST1009)
            </button>
          </div>

          {/* WebSocket Status */}
          <div className="flex items-center gap-2 bg-slate-900/60 border border-slate-800 px-3 py-1.5 rounded-lg text-xs">
            <span className={`h-2.5 w-2.5 rounded-full ${
              wsStatus === "connected" ? "bg-emerald-500 animate-ping" : 
              wsStatus === "connecting" ? "bg-amber-500 animate-pulse" : "bg-red-500"
            }`} />
            <span className="text-slate-400 capitalize font-medium">
              WS: {wsStatus}
            </span>
          </div>

          {/* Manual Refresh */}
          <button
            onClick={() => fetchAllData()}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 disabled:opacity-50 text-xs font-medium transition-all"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            Sync
          </button>
        </div>
      </header>

      {/* Global Error message */}
      {error && (
        <div className="mb-6 p-4 rounded-xl border border-red-500/20 bg-red-950/20 text-red-400 text-sm flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 flex-shrink-0 mt-0.5 text-red-500" />
          <div>
            <span className="font-semibold">Backend Connection Issue:</span> {error}.
            Please ensure the FastAPI server is running on <code className="bg-red-950 px-1 py-0.5 rounded text-white text-xs">localhost:8000</code>.
          </div>
        </div>
      )}

      {/* KPI Stats Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <div className="bg-slate-900 border border-slate-800/80 p-4 rounded-xl flex items-center justify-between shadow-lg hover:border-slate-700/80 transition-all">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Unique Shoppers</p>
            <h3 className="text-2xl font-bold text-slate-100 mt-1">{metrics?.unique_visitors ?? "—"}</h3>
            <span className="text-[10px] text-emerald-400 font-semibold flex items-center gap-1 mt-1">
              <TrendingUp className="h-2.5 w-2.5" /> Excluding Staff
            </span>
          </div>
          <div className="p-3 bg-purple-500/10 text-purple-400 rounded-xl">
            <Users className="h-6 w-6" />
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800/80 p-4 rounded-xl flex items-center justify-between shadow-lg hover:border-slate-700/80 transition-all">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Conversion Rate</p>
            <h3 className="text-2xl font-bold text-slate-100 mt-1">
              {metrics ? `${(metrics.conversion_rate * 100).toFixed(1)}%` : "—"}
            </h3>
            <span className="text-[10px] text-indigo-400 font-medium mt-1 block">5-min window correlation</span>
          </div>
          <div className="p-3 bg-indigo-500/10 text-indigo-400 rounded-xl">
            <Percent className="h-6 w-6" />
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800/80 p-4 rounded-xl flex items-center justify-between shadow-lg hover:border-slate-700/80 transition-all">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Queue Depth</p>
            <h3 className="text-2xl font-bold mt-1 text-slate-100">
              {metrics?.current_queue_depth ?? 0}
            </h3>
            <span className={`text-[10px] font-semibold mt-1 flex items-center gap-1 ${
              (metrics?.current_queue_depth ?? 0) > 4 ? "text-red-400" : "text-emerald-400"
            }`}>
              {(metrics?.current_queue_depth ?? 0) > 4 ? "Spike Detected: Add Staff" : "Queue Load: Normal"}
            </span>
          </div>
          <div className={`p-3 rounded-xl ${
            (metrics?.current_queue_depth ?? 0) > 4 ? "bg-red-500/10 text-red-400" : "bg-emerald-500/10 text-emerald-400"
          }`}>
            <Activity className="h-6 w-6" />
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800/80 p-4 rounded-xl flex items-center justify-between shadow-lg hover:border-slate-700/80 transition-all">
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Abandonment Rate</p>
            <h3 className="text-2xl font-bold text-slate-100 mt-1">
              {metrics ? `${(metrics.abandonment_rate * 100).toFixed(0)}%` : "—"}
            </h3>
            <span className="text-[10px] text-slate-400 block mt-1">Left checkout queue without paying</span>
          </div>
          <div className="p-3 bg-pink-500/10 text-pink-400 rounded-xl">
            <ShoppingCart className="h-6 w-6" />
          </div>
        </div>
      </div>

      {/* Anomalies alert banner */}
      {anomalies.length > 0 && (
        <div className="mb-6 p-4 bg-gradient-to-r from-amber-500/10 to-red-500/10 border border-amber-500/20 rounded-xl text-amber-300 text-xs flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-500 animate-bounce flex-shrink-0" />
          <div className="flex-1">
            <span className="font-semibold uppercase tracking-wider text-[10px] bg-amber-500/20 px-1.5 py-0.5 rounded mr-2">Anomaly</span>
            {anomalies[0].detail} · 
            {anomalies[0].suggested_action && (
              <span className="font-medium text-white ml-1">Action: {anomalies[0].suggested_action}</span>
            )}
          </div>
        </div>
      )}

      {/* Main Grid: Floorplan Heatmap, Funnel and Sidebar details */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Visual Floor Heatmap (2D plan) */}
        <div className="lg:col-span-2 bg-slate-900 border border-slate-800/80 rounded-xl shadow-xl overflow-hidden">
          <div className="border-b border-slate-800 px-4 py-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Eye className="h-4 w-4 text-purple-400" />
              <h2 className="text-sm font-bold uppercase tracking-wider text-slate-300">Live 2D Floorplan Heatmap</h2>
            </div>
            <span className="text-[10px] text-slate-400 italic">Hover or click a zone to inspect</span>
          </div>

          <div className="p-6">
            <div className="relative w-full aspect-[16/10] bg-slate-950 border border-slate-800 rounded-xl overflow-hidden">
              
              {/* Entries door marker */}
              <div className="absolute right-0 top-[35%] w-2.5 h-16 bg-purple-500 rounded-l-md flex items-center justify-center shadow-lg shadow-purple-500/30">
                <span className="text-[9px] font-bold text-white tracking-widest writing-mode-vertical uppercase">Door</span>
              </div>
              {storeId === "ST1009" && (
                <div className="absolute left-0 top-[35%] w-2.5 h-16 bg-purple-500 rounded-r-md flex items-center justify-center shadow-lg shadow-purple-500/30">
                  <span className="text-[9px] font-bold text-white tracking-widest writing-mode-vertical uppercase">Door 2</span>
                </div>
              )}

              {/* Grid zones overlay */}
              {zones.map((zone) => {
                const layout = activeLayout[zone.zone_id];
                if (!layout) return null;
                const heat = getHeatColor(zone.heat_score);
                const isSelected = selectedZone?.zone_id === zone.zone_id;

                return (
                  <div
                    key={zone.zone_id}
                    onMouseEnter={() => setHoveredZone({ zone, layout })}
                    onMouseLeave={() => setHoveredZone(null)}
                    onClick={() => setSelectedZone(isSelected ? null : zone)}
                    style={{
                      position: "absolute",
                      left: `${layout.x}%`,
                      top: `${layout.y}%`,
                      width: `${layout.w}%`,
                      height: `${layout.h}%`
                    }}
                    className={`rounded-lg border-2 ${heat.border} ${heat.bg} cursor-pointer transition-all duration-300 flex flex-col items-center justify-center p-2 text-center select-none shadow-md ${heat.glow} ${
                      isSelected ? "ring-2 ring-white ring-offset-2 ring-offset-slate-950 scale-[1.02] z-10" : "hover:scale-[1.01]"
                    }`}
                  >
                    <span className="text-xs font-bold text-slate-100 drop-shadow">{layout.label}</span>
                    <span className="text-xl font-extrabold mt-1 tracking-tight drop-shadow text-white">{zone.heat_score}</span>
                    <span className="text-[9px] opacity-80 font-medium hidden sm:inline drop-shadow text-white">Dwell: {formatDwell(zone.avg_dwell_ms)}</span>
                  </div>
                );
              })}

              {/* Tooltip Overlay */}
              {hoveredZone && (
                <div 
                  style={{
                    position: "absolute",
                    left: `${hoveredZone.layout.x > 60 ? hoveredZone.layout.x - 30 : hoveredZone.layout.x + hoveredZone.layout.w + 2}%`,
                    top: `${hoveredZone.layout.y}%`,
                  }}
                  className="z-30 bg-slate-900 border border-slate-700/80 p-3 rounded-lg shadow-xl min-w-[160px] pointer-events-none transition-all duration-150 animate-in fade-in"
                >
                  <p className="text-xs font-bold text-white border-b border-slate-800 pb-1.5 mb-1.5">{hoveredZone.layout.label}</p>
                  <div className="space-y-1 text-[10px]">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Heat Index:</span>
                      <span className="font-semibold text-orange-400">{hoveredZone.zone.heat_score}/100</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Total Visits:</span>
                      <span className="font-semibold text-white">{hoveredZone.zone.visit_count}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Avg Dwell:</span>
                      <span className="font-semibold text-emerald-400">{formatDwell(hoveredZone.zone.avg_dwell_ms)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Confidence:</span>
                      <span className={`font-semibold ${hoveredZone.zone.data_confidence === "HIGH" ? "text-emerald-400" : "text-amber-400"}`}>
                        {hoveredZone.zone.data_confidence}
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Heatmap Legend */}
            <div className="flex items-center justify-between mt-4 bg-slate-900/40 border border-slate-800/80 px-4 py-2.5 rounded-lg text-xs">
              <span className="text-slate-400 font-medium">Low Interaction</span>
              <div className="flex gap-1.5 flex-1 max-w-[200px] sm:max-w-[300px] mx-4">
                {["bg-blue-500/40", "bg-emerald-500/60", "bg-yellow-500/70", "bg-orange-500/80", "bg-red-500/80"].map((c, idx) => (
                  <div key={idx} className={`h-2 flex-1 rounded-sm ${c}`} />
                ))}
              </div>
              <span className="text-slate-400 font-medium">Hot zone</span>
            </div>
          </div>
        </div>

        {/* Sidebar details / Selected Zone Panel */}
        <div className="bg-slate-900 border border-slate-800/80 rounded-xl shadow-xl flex flex-col overflow-hidden">
          <div className="border-b border-slate-800 px-4 py-3">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <Layers className="h-4 w-4 text-purple-400" />
              Zone Inspector
            </h2>
          </div>
          
          <div className="p-4 flex-1 flex flex-col justify-between">
            {selectedZone ? (
              <div>
                <div className="flex items-start justify-between border-b border-slate-800 pb-3 mb-3">
                  <div>
                    <h3 className="font-bold text-white text-base">{activeLayout[selectedZone.zone_id]?.label || selectedZone.zone_id}</h3>
                    <p className="text-[10px] text-purple-400 font-semibold">{activeLayout[selectedZone.zone_id]?.category || "Retail Area"}</p>
                  </div>
                  <button 
                    onClick={() => setSelectedZone(null)}
                    className="text-[10px] px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400"
                  >
                    Deselect
                  </button>
                </div>
                
                <div className="grid grid-cols-2 gap-3 mb-4">
                  <div className="bg-slate-950 p-2.5 rounded-lg border border-slate-850">
                    <p className="text-[10px] text-slate-400">Avg Dwell Time</p>
                    <p className="text-sm font-extrabold text-emerald-400 mt-0.5">{formatDwell(selectedZone.avg_dwell_ms)}</p>
                  </div>
                  <div className="bg-slate-950 p-2.5 rounded-lg border border-slate-850">
                    <p className="text-[10px] text-slate-400">Total Visits</p>
                    <p className="text-sm font-extrabold text-white mt-0.5">{selectedZone.visit_count} visits</p>
                  </div>
                </div>

                <div className="space-y-2 text-xs">
                  <div className="bg-slate-950/60 p-2.5 rounded-lg border border-slate-850 flex items-center justify-between">
                    <span className="text-slate-400 flex items-center gap-1.5"><Clock className="h-3.5 w-3.5" /> Heat Score</span>
                    <span className="font-bold text-orange-400 text-sm">{selectedZone.heat_score}%</span>
                  </div>
                  <div className="bg-slate-950/60 p-2.5 rounded-lg border border-slate-850 flex items-center justify-between">
                    <span className="text-slate-400 flex items-center gap-1.5"><Shield className="h-3.5 w-3.5" /> Data Confidence</span>
                    <span className={`font-bold flex items-center gap-1 ${
                      selectedZone.data_confidence === "HIGH" ? "text-emerald-400" : "text-amber-400"
                    }`}>
                      <CheckCircle className="h-3.5 w-3.5" /> {selectedZone.data_confidence}
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-center py-8 text-slate-500">
                <HelpCircle className="h-8 w-8 mx-auto mb-2 text-slate-600" />
                <p className="text-xs">Select a zone on the 2D floorplan above to view detailed KPIs, average dwells, and interaction stats.</p>
              </div>
            )}

            {/* Live Websockets Terminal Log */}
            <div className="mt-4 border-t border-slate-850 pt-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
                  <Terminal className="h-3.5 w-3.5 text-purple-400" /> Live Ingestion Feed
                </span>
                <span className="text-[9px] bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded font-semibold animate-pulse">Real-Time</span>
              </div>
              <div className="h-40 overflow-y-auto bg-slate-950 border border-slate-850 rounded-lg p-2 font-mono text-[9px] space-y-1.5 scrollbar-thin">
                {liveEvents.length > 0 ? (
                  liveEvents.map((evt) => (
                    <div key={evt.id} className="text-slate-300 border-b border-slate-900 pb-1 flex justify-between gap-1 items-start">
                      <div>
                        <span className="text-purple-400 font-bold mr-1">[{evt.timestamp}]</span>
                        <span className="text-indigo-300 font-medium">{evt.visitor}</span>
                        <span className="text-slate-400 font-semibold mx-1">{evt.type}</span>
                        {evt.zone && <span className="text-slate-500">@{evt.zone}</span>}
                      </div>
                      {evt.isStaff && (
                        <span className="text-[8px] bg-blue-500/20 text-blue-400 px-1 rounded flex-shrink-0 font-bold">STAFF</span>
                      )}
                    </div>
                  ))
                ) : (
                  <div className="text-slate-600 text-center py-12 italic">
                    Waiting for events... Ingest data using the Python scripts or API requests to watch events propagate live.
                  </div>
                )}
              </div>
            </div>

          </div>
        </div>

      </div>

      {/* Recharts funnel and charts row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
        
        {/* Real-time Shopper Funnel */}
        <div className="bg-slate-900 border border-slate-800/80 p-4 rounded-xl shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-300">Shopper Funnel Conversion & Drop-off</h2>
            <span className="text-[10px] text-slate-400">Total sessions: {funnel?.session_count ?? 0}</span>
          </div>

          <div className="h-64">
            {funnelChartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={funnelChartData}
                  margin={{ top: 10, right: 30, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="funnelColor" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0.05}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="name" stroke="#64748b" tick={{ fontSize: 10 }} />
                  <YAxis stroke="#64748b" tick={{ fontSize: 10 }} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: 8, color: '#f3f4f6', fontSize: 11 }}
                    labelClassName="text-slate-300 font-bold"
                  />
                  <Legend verticalAlign="top" height={36} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
                  <Area type="monotone" dataKey="visitors" stroke="#8b5cf6" fillOpacity={1} fill="url(#funnelColor)" strokeWidth={2} name="Active Shoppers" />
                  <Area type="monotone" dataKey="Drop Off" stroke="#ec4899" fill="none" strokeWidth={1.5} name="Drop Off %" strokeDasharray="4 4" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-slate-500 text-xs italic">
                Loading funnel metrics...
              </div>
            )}
          </div>
        </div>

        {/* Avg Dwell Time per Zone */}
        <div className="bg-slate-900 border border-slate-800/80 p-4 rounded-xl shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
            <h2 className="text-sm font-bold uppercase tracking-wider text-slate-300">Average Dwell Time by Retail Zone</h2>
            <span className="text-[10px] text-slate-400">Excluding navigation zones</span>
          </div>

          <div className="h-64">
            {dwellChartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={dwellChartData}
                  layout="vertical"
                  margin={{ top: 5, right: 30, left: 10, bottom: 5 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis type="number" stroke="#64748b" tick={{ fontSize: 10 }} unit="s" />
                  <YAxis dataKey="name" type="category" stroke="#64748b" tick={{ fontSize: 10 }} width={120} />
                  <Tooltip
                    formatter={(v) => [`${v} seconds`, "Avg Dwell"]}
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: 8, fontSize: 11 }}
                  />
                  <Bar dataKey="dwell" radius={[0, 4, 4, 0]} name="Avg Dwell Time">
                    {dwellChartData.map((entry, idx) => {
                      const heat = getHeatColor(entry.heat);
                      return <Cell key={idx} fill={heat.bg.replace("rgba", "rgb").split(",")[0] + ")"} />;
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-slate-500 text-xs italic">
                No active dwell events recorded for this store.
              </div>
            )}
          </div>
        </div>

      </div>

      {/* Footer */}
      <footer className="mt-8 border-t border-slate-900 pt-4 flex flex-col sm:flex-row justify-between items-center text-[10px] text-slate-600 gap-2">
        <div>
          Real-Time Store Intelligence Panel v1.2.0 · React + FastAPI + WebSockets
        </div>
        <div>
          Resume Bullet Point: "Architected a real-time full-stack retail analytics dashboard using React, FastAPI, and WebSockets, rendering live customer dwell heatmaps and funnel metrics with sub-second update latencies."
        </div>
      </footer>

    </div>
  );
}
