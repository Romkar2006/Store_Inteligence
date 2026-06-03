import { useState, useEffect, useCallback } from "react"
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts"

const API_BASE = "http://localhost:8000"
const STORE_ID = "ST1008"
const REFRESH_MS = 5000

// Zone positions on the floorplan as percentages
// Matches the actual pixel-measured polygons from your cam_zones.py
const ZONE_LAYOUT = {
  MAKEUP_MIRROR:          { x: 2,  y: 18, w: 20, h: 38, label: "Mirror" },
  CENTER_AISLE:           { x: 24, y: 18, w: 16, h: 58, label: "Aisle" },
  SUMMER_DISPLAY:         { x: 24, y: 50, w: 20, h: 26, label: "Display" },
  MAKEUP_SHELF_MAIN:      { x: 42, y: 10, w: 30, h: 55, label: "Makeup main" },
  MAKEUP_SHELF_PREMIUM:   { x: 74, y: 10, w: 24, h: 55, label: "Makeup prem." },
  SKINCARE_SHELF_LEFT:    { x: 2,  y: 60, w: 20, h: 38, label: "Skincare" },
  BILLING_COUNTER:        { x: 40, y: 70, w: 56, h: 28, label: "Billing" },
}

// Heat score → background colour (green ramp, low=light, high=dark)
function heatColor(score) {
  if (score >= 90) return { bg: "#085041", text: "#9FE1CB" }
  if (score >= 70) return { bg: "#0F6E56", text: "#9FE1CB" }
  if (score >= 50) return { bg: "#1D9E75", text: "#E1F5EE" }
  if (score >= 30) return { bg: "#5DCAA5", text: "#085041" }
  if (score >= 15) return { bg: "#9FE1CB", text: "#085041" }
  return { bg: "#E1F5EE", text: "#085041" }
}

function fmtDwell(ms) {
  if (!ms) return "0s"
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  return `${Math.round(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

// ── Floorplan zone block ─────────────────────────────────────────────────────
function ZoneBlock({ zone, layout, onHover, onLeave }) {
  const { bg, text } = heatColor(zone.heat_score)
  return (
    <div
      onMouseEnter={(e) => onHover(zone, layout, e)}
      onMouseLeave={onLeave}
      style={{
        position: "absolute",
        left: `${layout.x}%`, top: `${layout.y}%`,
        width: `${layout.w}%`, height: `${layout.h}%`,
        background: bg, borderRadius: 4,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        cursor: "pointer", transition: "opacity .2s",
      }}
    >
      <span style={{ fontSize: 13, fontWeight: 500, color: text }}>
        {zone.heat_score}
      </span>
      <span style={{ fontSize: 9, color: text, textAlign: "center",
        lineHeight: 1.2, padding: "0 4px" }}>
        {layout.label}
      </span>
    </div>
  )
}

// ── Tooltip ──────────────────────────────────────────────────────────────────
function HoverTooltip({ zone, layout, pos }) {
  if (!zone) return null
  return (
    <div style={{
      position: "absolute",
      left: pos.x > 60 ? "auto" : pos.x + 8,
      right: pos.x > 60 ? `${100 - pos.x}%` : "auto",
      top: pos.y,
      background: "white", border: "0.5px solid #e0e0e0",
      borderRadius: 8, padding: "8px 12px",
      fontSize: 12, zIndex: 20, minWidth: 140,
      pointerEvents: "none",
    }}>
      <div style={{ fontWeight: 500, marginBottom: 4 }}>{layout.label}</div>
      {[
        ["Heat score",  `${zone.heat_score}/100`],
        ["Avg dwell",   fmtDwell(zone.avg_dwell_ms)],
        ["Visits",      zone.visit_count],
        ["Confidence",  zone.data_confidence],
      ].map(([k, v]) => (
        <div key={k} style={{ display: "flex", justifyContent: "space-between",
          gap: 16, color: "#666", marginTop: 2 }}>
          <span>{k}</span>
          <span style={{ fontWeight: 500, color: "#111" }}>{v}</span>
        </div>
      ))}
    </div>
  )
}

// ── KPI card ─────────────────────────────────────────────────────────────────
function KpiCard({ label, value, sub, subColor }) {
  return (
    <div style={{ padding: "12px 16px", borderRight: "0.5px solid #e5e7eb" }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 500 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: subColor || "#888",
        marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

// ── Main dashboard ───────────────────────────────────────────────────────────
export default function App() {
  const [heatmap,  setHeatmap]  = useState(null)
  const [metrics,  setMetrics]  = useState(null)
  const [anomalies,setAnomalies]= useState([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)
  const [lastSync, setLastSync] = useState(null)
  const [countdown,setCountdown]= useState(5)
  const [hovered,  setHovered]  = useState(null)   // { zone, layout, pos }

  const fetchAll = useCallback(async () => {
    try {
      setError(null)
      const [hmRes, mRes, aRes] = await Promise.all([
        fetch(`${API_BASE}/stores/${STORE_ID}/heatmap`),
        fetch(`${API_BASE}/stores/${STORE_ID}/metrics`),
        fetch(`${API_BASE}/stores/${STORE_ID}/anomalies`),
      ])
      if (!hmRes.ok) throw new Error(`API ${hmRes.status}`)
      setHeatmap(await hmRes.json())
      setMetrics(await mRes.json())
      const aData = await aRes.json()
      setAnomalies(aData.anomalies || [])
      setLastSync(new Date())
      setCountdown(5)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial fetch + 5-second interval
  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, REFRESH_MS)
    return () => clearInterval(interval)
  }, [fetchAll])

  // Countdown ticker
  useEffect(() => {
    const t = setInterval(() => {
      setCountdown(c => c <= 1 ? 5 : c - 1)
    }, 1000)
    return () => clearInterval(t)
  }, [lastSync])

  const zones = heatmap?.zones || []
  const sortedZones = [...zones].sort((a, b) => b.heat_score - a.heat_score)
  const chartData = sortedZones.map(z => ({
    name: ZONE_LAYOUT[z.zone_id]?.label || z.zone_id,
    dwell: Math.round(z.avg_dwell_ms / 1000),
    heat: z.heat_score,
  }))

  const queueDepth = metrics?.current_queue_depth || 0
  const hasSpike = queueDepth > 4 || anomalies.length > 0

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", background: "#f9fafb",
      minHeight: "100vh", padding: 20 }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center",
        justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 500, margin: 0 }}>
            Store Intelligence Dashboard
          </h1>
          <div style={{ fontSize: 12, color: "#888", marginTop: 2 }}>
            Purplle Brigade Road · ST1008 · Live zone heatmap
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {error && (
            <span style={{ fontSize: 12, color: "#dc2626",
              background: "#fef2f2", padding: "4px 10px", borderRadius: 6 }}>
              API error: {error}
            </span>
          )}
          <div style={{ width: 8, height: 8, borderRadius: "50%",
            background: error ? "#dc2626" : "#10b981" }} />
          <span style={{ fontSize: 12, color: "#888" }}>
            {loading ? "Loading..." : `Next refresh in ${countdown}s`}
          </span>
          <button onClick={fetchAll}
            style={{ fontSize: 12, padding: "5px 12px", borderRadius: 6,
              border: "0.5px solid #d1d5db", background: "white",
              cursor: "pointer" }}>
            Refresh now
          </button>
        </div>
      </div>

      {/* KPI row */}
      <div style={{ background: "white", border: "0.5px solid #e5e7eb",
        borderRadius: 12, display: "grid",
        gridTemplateColumns: "repeat(4,1fr)", marginBottom: 16 }}>
        <KpiCard label="Unique visitors"
          value={metrics?.unique_visitors ?? "—"}
          sub="customers today" />
        <KpiCard label="Conversion rate"
          value={metrics ? `${(metrics.conversion_rate * 100).toFixed(1)}%` : "—"}
          sub="2-min clip window" />
        <KpiCard label="Queue depth"
          value={queueDepth}
          sub={queueDepth > 4 ? "Spike — add staff" : "Normal"}
          subColor={queueDepth > 4 ? "#dc2626" : "#10b981"} />
        <KpiCard label="Abandonment rate"
          value={metrics ? `${Math.round(metrics.abandonment_rate * 100)}%` : "—"}
          sub="of billing visitors"
          style={{ borderRight: "none" }} />
      </div>

      {/* Anomaly banner */}
      {hasSpike && (
        <div style={{ background: "#fef3c7", border: "0.5px solid #fbbf24",
          borderRadius: 8, padding: "10px 14px", marginBottom: 16,
          fontSize: 13, color: "#92400e", display: "flex",
          alignItems: "center", gap: 8 }}>
          ⚠ {anomalies[0]?.detail || `Queue depth {queueDepth} — above threshold of 4`}
          {anomalies[0]?.suggested_action && (
            <span style={{ color: "#78350f" }}>
              · {anomalies[0].suggested_action}
            </span>
          )}
        </div>
      )}

      {/* Main grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 280px",
        gap: 16 }}>

        {/* Floorplan */}
        <div style={{ background: "white", border: "0.5px solid #e5e7eb",
          borderRadius: 12, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", borderBottom: "0.5px solid #e5e7eb",
            fontSize: 12, fontWeight: 500, color: "#888",
            letterSpacing: ".06em", textTransform: "uppercase",
            display: "flex", justifycontent: "space-between",
            alignItems: "center" }}>
            Zone heatmap
            <span style={{ fontWeight: 400, color: "#bbb" }}>
              hover a zone for detail
            </span>
          </div>
          <div style={{ position: "relative", margin: 16,
            aspectRatio: "3/2", background: "#f3f4f6", borderRadius: 8 }}>
            {zones.map(zone => {
              const layout = ZONE_LAYOUT[zone.zone_id]
              if (!layout) return null
              return (
                <ZoneBlock key={zone.zone_id} zone={zone} layout={layout}
                  onHover={(z, l, e) => {
                    const rect = e.currentTarget
                      .closest('[style*="aspect-ratio"]')
                      .getBoundingClientRect()
                    setHovered({
                      zone: z, layout: l,
                      pos: {
                        x: ((e.clientX - rect.left) / rect.width) * 100,
                        y: e.clientY - rect.top,
                      }
                    })
                  }}
                  onLeave={() => setHovered(null)}
                />
              )
            })}
            {hovered && (
              <HoverTooltip zone={hovered.zone}
                layout={hovered.layout} pos={hovered.pos} />
            )}
            {/* Entry door marker */}
            <div style={{ position: "absolute", right: 0, top: "35%",
              width: 12, height: 18, background: "#7B2FBE",
              borderRadius: "4px 0 0 4px", display: "flex",
              alignItems: "center", justifyContent: "center" }}>
              <span style={{ fontSize: 7, color: "white",
                writingMode: "vertical-rl" }}>IN</span>
            </div>
          </div>

          {/* Legend */}
          <div style={{ display: "flex", alignItems: "center",
            gap: 8, padding: "8px 16px 14px",
            borderTop: "0.5px solid #e5e7eb" }}>
            <span style={{ fontSize: 11, color: "#888" }}>Low activity</span>
            {["#E1F5EE","#9FE1CB","#5DCAA5","#1D9E75","#0F6E56","#085041"]
              .map(c => (
                <div key={c} style={{ flex: 1, height: 8,
                  background: c, borderRadius: 2 }} />
              ))}
            <span style={{ fontSize: 11, color: "#888" }}>High activity</span>
          </div>
        </div>

        {/* Sidebar */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

          {/* Zone ranking */}
          <div style={{ background: "white", border: "0.5px solid #e5e7eb",
            borderRadius: 12, padding: "12px 14px" }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "#888",
              letterSpacing: ".06em", textTransform: "uppercase",
              marginBottom: 10 }}>Zone ranking</div>
            {sortedZones.map(zone => {
              const layout = ZONE_LAYOUT[zone.zone_id]
              const { bg } = heatColor(zone.heat_score)
              return (
                <div key={zone.zone_id} style={{ display: "flex",
                  alignItems: "center", gap: 8, padding: "6px 0",
                  borderBottom: "0.5px solid #f0f0f0" }}>
                  <div style={{ width: 10, height: 10, borderRadius: 2,
                    background: bg, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, color: "#555", flex: 1,
                    lineHeight: 1.3 }}>
                    {layout?.label || zone.zone_id}
                  </span>
                  <div style={{ width: 52, height: 5,
                    background: "#f0f0f0", borderRadius: 3,
                    overflow: "hidden" }}>
                    <div style={{ width: `${zone.heat_score}%`, height: 5,
                      background: bg, borderRadius: 3,
                      transition: "width .6s ease" }} />
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 500,
                    color: bg, minWidth: 24, textAlign: "right" }}>
                    {zone.heat_score}
                  </span>
                </div>
              )
            })}
          </div>

          {/* Dwell time chart */}
          <div style={{ background: "white", border: "0.5px solid #e5e7eb",
            borderRadius: 12, padding: "12px 14px" }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "#888",
              letterSpacing: ".06em", textTransform: "uppercase",
              marginBottom: 10 }}>Avg dwell (seconds)</div>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={chartData} layout="vertical"
                margin={{ left: 0, right: 8, top: 0, bottom: 0 }}>
                <XAxis type="number" tick={{ fontSize: 10 }} axisLine={false}
                  tickLine={false} />
                <YAxis type="category" dataKey="name" width={72}
                  tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip
                  formatter={(v) => [`${v}s`, "Avg dwell"]}
                  contentStyle={{ fontSize: 12, borderRadius: 6,
                    border: "0.5px solid #e0e0e0" }} />
                  <Bar dataKey="dwell" radius={[0, 3, 3, 0]}>
                    {chartData.map((entry, i) => (
                      <Cell key={i} fill={heatColor(entry.heat).bg} />
                    ))}
                  </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

        </div>
      </div>

      {/* Footer */}
      <div style={{ marginTop: 12, fontSize: 11, color: "#bbb",
        textAlign: "center" }}>
        Last synced: {lastSync?.toLocaleTimeString() || "—"} ·
        Auto-refreshes every {REFRESH_MS / 1000}s
      </div>

    </div>
  )
}
