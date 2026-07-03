/* Axle ERP — responsive frames: tablet 768 (icon rail) + mobile 390 (lookup & quote). */

/* Tablet catalog: rail sidebar, condensed columns */
function TabletCatalog() {
  const rows = partRows.slice(0, 9);
  return (
    <div className="erp dark rail-mode" data-screen-label="Parts catalog · 768 tablet">
      <Sidebar chrome="dark" active="inventory" rail />
      <div className="ws">
        <div className="tb" style={{ padding: "0 14px", gap: 10 }}>
          <Crumb items={["Inventory"]} />
          <span className="grow"></span>
          <Btn kind="pri" sm ic={I.plus}>New Part</Btn>
        </div>
        <div className="body" style={{ padding: "14px 14px" }}>
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="fbar">
              <span style={{ flex: 1, display: "flex" }}><Search ph="Search parts…" grow /></span>
              <span className="fchip on">All</span>
              <span className="fchip">Low</span>
            </div>
            <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
              <div style={{ flex: 1, overflow: "hidden" }}>
                <table className="tbl">
                  <thead><tr>
                    <th>Part #</th><th>Description</th><th className="r">Avail</th><th className="r">List</th><th>Status</th>
                  </tr></thead>
                  <tbody>
                    {rows.map((p) => (
                      <tr key={p.pn}>
                        <td className="pn">{p.pn}</td>
                        <td className="desc" style={{ maxWidth: 210, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.desc}</td>
                        <td className="r mono" style={{ fontWeight: 600 }}>{p.av}</td>
                        <td className="r money">{p.list}</td>
                        <td><span className={"stk " + p.st}><span className="d"></span>{p.st === "ok" ? "In stock" : p.st === "low" ? "Low" : "Out"}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pgr"><span>4,182 parts</span><span style={{ marginLeft: "auto" }}></span><span className="pg on">1</span><span className="pg">2</span><span>…</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* Mobile: part lookup — the road use-case. Big targets (>=44px). */
function MobileLookup() {
  const parts = [
    { pn: "10R-1000", ds: "Reman Injector — CAT C15", av: 8, bin: "A-04-2", pr: "$615.00", st: "ok", core: "+$100 core" },
    { pn: "10R-7222", ds: "Reman Injector — CAT C7", av: 6, bin: "A-04-5", pr: "$529.00", st: "ok", core: "+$85 core" },
    { pn: "OR-9352", ds: "Reman Turbo — CAT 3406E", av: 0, bin: "A-10-1", pr: "$2,495.00", st: "low", core: "+$300 core" },
  ];
  return (
    <div className="mob" data-screen-label="Part lookup · 390 mobile">
      <div className="mtb">
        <span className="mk"><Hub simple /></span>
        <span className="w">Axle</span>
        <span className="who">Front Counter</span>
      </div>
      <div className="mbody">
        <div className="srch" style={{ padding: "13px 14px" }}>
          <span className="ic">{I.search}</span>
          <span className="ph" style={{ fontSize: 15, color: "var(--ink)" }}>10R injector</span>
          <span className="ic" style={{ color: "var(--mil)" }}>{I.scan}</span>
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--steel-400)" }}>3 results · in CAT · Fuel</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, overflow: "hidden" }}>
          {parts.map((p) => (
            <div className="pcard" key={p.pn}>
              <div className="top">
                <div>
                  <div className="pn">{p.pn}</div>
                  <div className="ds">{p.ds}</div>
                </div>
                <div className="pr">{p.pr}<div style={{ fontSize: 10, fontWeight: 400, color: "#b06511" }}>{p.core}</div></div>
              </div>
              <div className="meta">
                <span className={"stk " + p.st}><span className="d"></span>{p.av > 0 ? p.av + " avail" : "0 avail"}</span>
                <span>Bin {p.bin}</span>
              </div>
              <div className="act">
                <span className="btn ghost" style={{ justifyContent: "center" }}>Details</span>
                <span className="btn pri" style={{ justifyContent: "center" }}>Add to Quote</span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="mnav">
        <a><span className="ic">{I.search}</span>Lookup</a>
        <a className="on"><span className="ic">{I.quote}</span>Quotes</a>
        <a><span className="ic">{I.core}</span>Cores</a>
        <a><span className="ic">{I.cust}</span>Customers</a>
      </div>
    </div>
  );
}

/* Mobile quote summary — read-and-act-light */
function MobileQuote() {
  return (
    <div className="mob" data-screen-label="Quote view · 390 mobile">
      <div className="mtb">
        <span className="mk"><Hub simple /></span>
        <span className="w" style={{ fontSize: 16 }}>Q-2241</span>
        <span className="chip draft" style={{ marginLeft: 8 }}><span className="dot"></span>Draft</span>
        <span className="who">Rocky Mtn Freight</span>
      </div>
      <div className="mbody">
        <div className="card">
          <div className="chd"><h4>5 Lines · 9 Units</h4><span className="r">edited 4:12 pm</span></div>
          {quoteLines.slice(0, 4).map((l) => (
            <div key={l.n} style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", borderBottom: "1px solid var(--steel-100)" }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="pn" style={{ fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 13, color: "var(--mil-deep)" }}>{l.pn}</div>
                <div style={{ fontSize: 12, color: "var(--steel-500)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{l.desc}</div>
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--steel-400)" }}>×{l.qty}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>{l.ext}</div>
            </div>
          ))}
          <div style={{ padding: "9px 14px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--steel-400)" }}>+1 more line…</div>
        </div>
        <div className="card">
          <div className="tot" style={{ borderRadius: 0 }}><span className="l">Quote Total</span><span className="v">$6,485.75</span></div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <span className="btn ghost" style={{ flex: 1, justifyContent: "center", padding: "13px 0" }}>Print</span>
          <span className="btn amb" style={{ flex: 1.6, justifyContent: "center", padding: "13px 0" }}>Convert to Invoice</span>
        </div>
      </div>
      <div className="mnav">
        <a><span className="ic">{I.search}</span>Lookup</a>
        <a className="on"><span className="ic">{I.quote}</span>Quotes</a>
        <a><span className="ic">{I.core}</span>Cores</a>
        <a><span className="ic">{I.cust}</span>Customers</a>
      </div>
    </div>
  );
}

Object.assign(window, { TabletCatalog, MobileLookup, MobileQuote });
