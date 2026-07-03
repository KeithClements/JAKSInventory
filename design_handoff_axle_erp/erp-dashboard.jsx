/* Axle ERP — Dashboard @1280, dark chrome. DashBody is reusable by the prototype. */

const dashQuotes = [
  { q: "Q-2241", cust: "Rocky Mtn Freight", age: "today", total: "6,485.75", st: "draft", stl: "Draft" },
  { q: "Q-2240", cust: "Hi-Plains Ag Service", age: "1d", total: "1,847.00", st: "ok", stl: "Sent" },
  { q: "Q-2238", cust: "Westline Towing", age: "2d", total: "11,420.50", st: "ok", stl: "Sent" },
  { q: "Q-2236", cust: "Mesa Verde Logistics", age: "4d", total: "3,212.25", st: "low", stl: "Expiring" },
  { q: "Q-2233", cust: "Front Range Disposal", age: "6d", total: "894.00", st: "low", stl: "Expiring" },
];

const dashAlerts = [
  { k: "low", t: "EGR cooler price expires 06/15", d: "Q-2241 · line 3 — vendor cost change" },
  { k: "low", t: "3 cores due back", d: "oldest 9 days · $945.00 held" },
  { k: "out", t: "3804726 out of stock", d: "Head Gasket Set — N14 · 2 on order, ETA 06/16" },
  { k: "ok", t: "PO-1182 received", d: "12 lines into bin · putaway complete" },
];

function Kpi({ label, value, sub, accent }) {
  return (
    <div className="card" style={{ flex: 1, padding: "14px 16px" }}>
      <div style={{ fontFamily: "var(--font-disp)", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".08em", fontSize: 10.5, color: "var(--steel-400)" }}>{label}</div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 24, fontWeight: 600, marginTop: 6, color: accent || "var(--ink)" }}>{value}</div>
      <div style={{ fontSize: 11.5, color: "var(--steel-500)", marginTop: 3 }}>{sub}</div>
    </div>
  );
}

function DashBody({ onOpenQuote }) {
  return (
    <div className="body" style={{ flexDirection: "column", overflow: "hidden" }}>
      <div style={{ display: "flex", gap: 14, flex: "0 0 auto" }}>
        <Kpi label="Open quotes" value="$48,210" sub="14 quotes · 5 expiring this week" />
        <Kpi label="Cores due back" value="3" sub="$945.00 held · oldest 9 days" accent="#b06511" />
        <Kpi label="Low / out of stock" value="12" sub="3 on open POs" accent="var(--danger)" />
        <Kpi label="Invoiced MTD" value="$212,480" sub="QuickBooks synced 2 min ago" accent="var(--mil)" />
      </div>
      <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
        <div className="card" style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div className="chd"><h4>Open Quotes</h4><span className="r">sorted by age</span></div>
          <div style={{ flex: 1, overflow: "hidden" }}>
            <table className="tbl">
              <thead><tr><th>Quote</th><th>Customer</th><th>Age</th><th className="r">Total</th><th>Status</th></tr></thead>
              <tbody>
                {dashQuotes.map((r) => (
                  <tr key={r.q} onClick={onOpenQuote ? () => onOpenQuote(r.q) : undefined} style={onOpenQuote ? { cursor: "pointer" } : null}>
                    <td className="pn">{r.q}</td>
                    <td className="desc">{r.cust}</td>
                    <td className="mono" style={{ color: "var(--steel-400)" }}>{r.age}</td>
                    <td className="r money ext">{r.total}</td>
                    <td><span className={"stk " + (r.st === "draft" ? "" : r.st)}><span className="d" style={r.st === "draft" ? { background: "var(--steel-300)" } : null}></span>{r.stl}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="rail" style={{ width: 320 }}>
          <div className="card" style={{ flex: 1 }}>
            <div className="chd"><h4>Needs Attention</h4><span className="r">4 items</span></div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {dashAlerts.map((a, i) => (
                <div key={i} style={{ display: "flex", gap: 10, padding: "11px 14px", borderBottom: i < dashAlerts.length - 1 ? "1px solid var(--steel-100)" : "none" }}>
                  <span className={"stk " + a.k} style={{ paddingTop: 4 }}><span className="d"></span></span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 12.5 }}>{a.t}</div>
                    <div style={{ fontSize: 11.5, color: "var(--steel-500)", marginTop: 2 }}>{a.d}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="card"><div className="sync"><span className="led"></span><span><b>QuickBooks</b> synced</span><span className="t">2 min ago</span></div></div>
        </div>
      </div>
    </div>
  );
}

function DashScreen() {
  return (
    <div className="erp dark" data-screen-label="Dashboard · 1280">
      <Sidebar chrome="dark" active="dash" />
      <div className="ws">
        <div className="tb">
          <Crumb items={["Dashboard"]} />
          <span className="grow"></span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--steel-400)" }}>Thu Jun 11 · Front Counter</span>
        </div>
        <DashBody />
      </div>
    </div>
  );
}

Object.assign(window, { DashScreen, DashBody, dashQuotes });
