/* Axle ERP — Parts catalog / inventory @1280, dense table + detail drawer. */

const partRows = [
  { pn: "10R-1000", desc: "Reman Injector — CAT C15 Acert", cat: "Fuel", bin: "A-04-2", oh: 12, com: 4, av: 8, core: "100.00", cost: "412.10", list: "615.00", st: "ok", sel: true },
  { pn: "10R-7222", desc: "Reman Injector — CAT C7", cat: "Fuel", bin: "A-04-5", oh: 6, com: 0, av: 6, core: "85.00", cost: "355.00", list: "529.00", st: "ok" },
  { pn: "4089826", desc: "Water Pump — Cummins ISX", cat: "Cooling", bin: "B-11-4", oh: 3, com: 1, av: 2, core: "—", cost: "196.20", list: "289.50", st: "low" },
  { pn: "23538522", desc: "EGR Cooler — Detroit S60 14L", cat: "Emissions", bin: "C-02-1", oh: 2, com: 1, av: 1, core: "250.00", cost: "801.00", list: "1,184.00", st: "low" },
  { pn: "1830560C92", desc: "Turbocharger — Navistar DT466E", cat: "Air", bin: "A-09-3", oh: 4, com: 1, av: 3, core: "175.00", cost: "644.50", list: "947.25", st: "ok" },
  { pn: "BOS-0445120007", desc: "Common-Rail Injector — Bosch", cat: "Fuel", bin: "D-01-6", oh: 9, com: 2, av: 7, core: "60.00", cost: "212.40", list: "318.00", st: "ok" },
  { pn: "3804726", desc: "Head Gasket Set — Cummins N14", cat: "Gaskets", bin: "E-03-2", oh: 0, com: 0, av: 0, core: "—", cost: "118.75", list: "176.00", st: "out" },
  { pn: "23532333", desc: "Oil Cooler — Detroit S60", cat: "Cooling", bin: "C-05-4", oh: 5, com: 0, av: 5, core: "40.00", cost: "203.00", list: "298.00", st: "ok" },
  { pn: "OR-9352", desc: "Reman Turbo — CAT 3406E", cat: "Air", bin: "A-10-1", oh: 1, com: 1, av: 0, core: "300.00", cost: "1,710.00", list: "2,495.00", st: "low" },
  { pn: "4955229", desc: "Injector Harness — Cummins ISC", cat: "Electrical", bin: "F-01-3", oh: 14, com: 0, av: 14, core: "—", cost: "67.30", list: "104.00", st: "ok" },
  { pn: "23512896", desc: "Cam Follower Kit — Detroit S60", cat: "Engine", bin: "E-07-1", oh: 7, com: 2, av: 5, core: "—", cost: "88.90", list: "139.00", st: "ok" },
];

function CatalogScreen({ chrome = "dark" }) {
  return (
    <div className={"erp " + chrome} data-screen-label="Parts catalog · 1280">
      <Sidebar chrome={chrome} active="inventory" />
      <div className="ws">
        <div className="tb">
          <Crumb items={["Inventory", "All Parts"]} />
          <span className="grow"></span>
          <Btn ic={I.print}>Export</Btn>
          <Btn kind="pri" ic={I.plus}>New Part</Btn>
        </div>
        <div className="body">
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="fbar">
              <span style={{ flex: 1, minWidth: 220, display: "flex" }}><Search ph="Search 4,182 parts… number, description, cross-ref" kbd="/" grow /></span>
              <span className="fchip on">All</span>
              <span className="fchip">Fuel</span>
              <span className="fchip">Air</span>
              <span className="fchip">Cooling</span>
              <span className="fchip">Low stock</span>
              <span className="fchip">Cores due</span>
            </div>
            <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
              <div style={{ flex: 1, overflow: "hidden" }}>
                <table className="tbl">
                  <thead><tr>
                    <th>Part #</th><th>Description</th><th>Bin</th>
                    <th className="r">On hand</th><th className="r">Comm.</th><th className="r">Avail</th>
                    <th className="r">Core</th><th className="r">List</th><th>Status</th>
                  </tr></thead>
                  <tbody>
                    {partRows.map((p) => (
                      <tr key={p.pn} className={p.sel ? "sel" : ""}>
                        <td className="pn">{p.pn}</td>
                        <td className="desc">{p.desc}</td>
                        <td className="mono" style={{ color: "var(--steel-400)" }}>{p.bin}</td>
                        <td className="r mono">{p.oh}</td>
                        <td className="r mono" style={{ color: "var(--steel-400)" }}>{p.com}</td>
                        <td className="r mono" style={{ fontWeight: 600 }}>{p.av}</td>
                        <td className="r money" style={{ color: p.core === "—" ? "var(--steel-300)" : "#b06511" }}>{p.core}</td>
                        <td className="r money">{p.list}</td>
                        <td><span className={"stk " + p.st}><span className="d"></span>{p.st === "ok" ? "In stock" : p.st === "low" ? "Low" : "Out"}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pgr">
                <span>4,182 parts</span>
                <span style={{ marginLeft: "auto" }}></span>
                <span className="pg on">1</span><span className="pg">2</span><span className="pg">3</span><span>…</span><span className="pg">381</span>
              </div>
            </div>
          </div>
          {/* detail drawer for selected row */}
          <div className="drawer">
            <div className="card">
              <div className="chd"><h4>Part Detail</h4><Chip kind="ok">In stock</Chip></div>
              <div style={{ padding: "12px 14px 6px" }}>
                <div className="bigpn">10R-1000</div>
                <div style={{ fontSize: 12.5, color: "var(--steel-600)", marginTop: 3, lineHeight: 1.5 }}>Reman Injector — CAT C15 Acert<br /><span style={{ color: "var(--steel-400)" }}>Cross: 20R-1308 · 10R-9787</span></div>
              </div>
              <div style={{ padding: "6px 0 4px" }}>
                <div className="kv"><span>List</span><b className="money">$615.00</b></div>
                <div className="kv"><span>Cost (avg)</span><span className="money">$412.10</span></div>
                <div className="kv"><span>Core charge</span><span className="money" style={{ color: "#b06511" }}>$100.00</span></div>
              </div>
            </div>
            <div className="card">
              <div className="chd"><h4>Stock by Location</h4><span className="r">12 on hand</span></div>
              <div className="loc">
                <span className="h">Bin</span><span className="h">On hand</span><span className="h">Comm.</span>
                <span className="mono">A-04-2 · Front</span><span className="mono">8</span><span className="mono">4</span>
                <span className="mono">W-201 · Warehouse</span><span className="mono">4</span><span className="mono">0</span>
              </div>
            </div>
            <div className="card">
              <div className="sync"><span className="led" style={{ background: "var(--amber)", boxShadow: "0 0 0 3px var(--amber-bg)" }}></span><span><b>3 cores</b> due back</span><span className="t">oldest 9 days</span></div>
            </div>
            <Btn kind="pri" ic={I.plus}>Add to Quote</Btn>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { CatalogScreen, partRows });
