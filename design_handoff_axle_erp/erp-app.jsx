/* Axle ERP — interactive prototype app. Reuses axle-erp.css + erp-shared/quote/catalog/dashboard data. */
const { useState, useMemo } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "chrome": "dark",
  "density": "regular",
  "showMargin": true
}/*EDITMODE-END*/;

/* clickable sidebar */
function AppSidebar({ chrome, active, onNav }) {
  const dark = chrome === "dark";
  const items = [
    ["dash", "Dashboard", I.dash],
    ["quote", "Quotes", I.quote, "14"],
    ["inventory", "Inventory", I.inv],
  ];
  return (
    <aside className="sb">
      <div className="brand">
        <span className="mk"><Hub ring={dark ? "#fff" : "var(--mil)"} bore={dark ? "var(--amber-2)" : "var(--amber)"} /></span>
        <span className="w">Axle<span className="s">Parts Counter</span></span>
      </div>
      <nav>
        {items.map(([k, label, ic, bdg]) => (
          <a key={k} className={k === active ? "on" : ""} onClick={() => onNav(k)} style={{ cursor: "pointer" }}>
            <span className="ic">{ic}</span>{label}
            {bdg && <span className="bdg">{bdg}</span>}
          </a>
        ))}
        <div style={{ margin: "10px 12px 4px", fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: ".14em", color: "var(--steel-500)", textTransform: "uppercase" }}>Prototype scope</div>
        {[["Cores", I.core], ["Purchasing", I.po], ["Customers", I.cust]].map(([label, ic]) => (
          <a key={label} style={{ opacity: .38 }}><span className="ic">{ic}</span>{label}</a>
        ))}
      </nav>
      <div className="foot"><span>Shop · <b>Front Counter</b><br />Sandra C. · Admin</span></div>
    </aside>
  );
}

const money = (n) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (s) => parseFloat(String(s).replace(/,/g, "")) || 0;

/* ----- Quote page: live qty + totals + convert ----- */
function QuotePage({ converted, setConverted, qtys, setQtys, showMargin }) {
  const lines = quoteLines.map((l) => ({ ...l, qty: qtys[l.n] ?? l.qty }));
  const sub = lines.reduce((a, l) => a + num(l.unit) * l.qty, 0);
  const cores = lines.reduce((a, l) => a + (l.core === "—" ? 0 : num(l.core) * l.qty), 0);
  const supplies = 24;
  const total = sub + cores + supplies;
  const units = lines.reduce((a, l) => a + l.qty, 0);
  const bump = (n, d) => setQtys((q) => ({ ...q, [n]: Math.max(0, (q[n] ?? quoteLines.find(x => x.n === n).qty) + d) }));
  return (
    <div className="ws">
      <div className="tb">
        <Crumb items={["Quotes", converted ? "INV-20486" : "Q-2241"]} />
        <Chip kind={converted ? "ok" : "draft"}>{converted ? "Invoice" : "Draft"}</Chip>
        <span className="grow"></span>
        <a href="Axle Quote Print.html" target="_blank" rel="noopener" style={{ display: "contents" }}><Btn ic={I.print}>Print</Btn></a>
        <Btn ic={I.save}>Save</Btn>
        {!converted && <span onClick={() => setConverted(true)} style={{ cursor: "pointer", display: "contents" }}><Btn kind="amb" ic={I.arrow}>Convert to Invoice</Btn></span>}
      </div>
      <div className="body">
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", flex: "0 0 auto" }}><Search ph="Add part — search by number, description, or scan…" kbd="/" grow /></div>
          <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="chd"><h4>Line Items</h4><span className="r">{lines.length} lines · {units} units</span></div>
            <div style={{ flex: 1, overflow: "auto" }}>
              <table className="tbl">
                <thead><tr>
                  <th style={{ width: 28 }}>#</th><th>Part</th><th>Bin</th>
                  <th style={{ width: 92 }}>Qty</th><th className="r">Unit</th><th className="r">Core</th><th className="r">Ext</th>
                </tr></thead>
                <tbody>
                  {lines.map((l) => (
                    <tr key={l.n} className={l.hot ? "hot" : ""}>
                      <td className="mono" style={{ color: "var(--steel-300)" }}>{l.n}</td>
                      <td><div className="pn">{l.pn}</div><div className="desc">{l.desc}</div></td>
                      <td className="mono" style={{ color: "var(--steel-400)" }}>{l.bin}</td>
                      <td><span className="qty">
                        <button className="pm" onClick={() => bump(l.n, -1)} style={{ cursor: "pointer" }}>−</button>
                        <span>{l.qty}</span>
                        <button className="pm" onClick={() => bump(l.n, 1)} style={{ cursor: "pointer" }}>+</button>
                      </span></td>
                      <td className="r money">{l.unit}</td>
                      <td className="r">{l.core === "—" ? <span className="money" style={{ color: "var(--steel-300)" }}>—</span> : <span className="corechip">+{l.core}</span>}</td>
                      <td className="r money ext">{money(num(l.unit) * l.qty)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ padding: "10px 14px", borderTop: "1px solid var(--steel-150)", display: "flex", gap: 8 }}>
              <Btn sm ic={I.plus}>Add Line</Btn>
              <Btn sm ic={I.scan}>Scan</Btn>
              <span className="alertchip" style={{ marginLeft: "auto", alignSelf: "center" }}><span className="ic">{I.warn}</span>Price expires 06/15 — line 3</span>
            </div>
          </div>
        </div>
        <div className="rail">
          <div className="card">
            <div className="chd"><h4>Customer</h4><span className="r">Acct RMF-1180</span></div>
            <div style={{ padding: "10px 14px 12px" }}>
              <div style={{ fontFamily: "var(--font-disp)", fontWeight: 600, fontSize: 15 }}>Rocky Mtn Freight</div>
              <div style={{ fontSize: 12, color: "var(--steel-500)", marginTop: 3, lineHeight: 1.5 }}>Net 30 · Tax-exempt (CO)<br />Dispatch: Carla V. — (303) 555-0177</div>
            </div>
          </div>
          <div className="card">
            <div className="chd"><h4>Totals</h4></div>
            <div style={{ padding: "6px 0" }}>
              <div className="kv"><span>Subtotal</span><span className="money">${money(sub)}</span></div>
              <div className="kv"><span>Core charges</span><span className="money" style={{ color: "#b06511" }}>${money(cores)}</span></div>
              <div className="kv"><span>Shop supplies</span><span className="money">${money(supplies)}</span></div>
              <div className="kv"><span>Tax</span><span className="money" style={{ color: "var(--steel-400)" }}>exempt</span></div>
            </div>
            <div className="tot"><span className="l">{converted ? "Invoice Total" : "Quote Total"}</span><span className="v">${money(total)}</span></div>
            {showMargin && <div className="margin">
              <div className="row"><span>Margin</span><b>27.4%</b></div>
              <div className="bar"><i style={{ width: "62%" }}></i></div>
            </div>}
          </div>
          <div className="card">
            <div className="sync"><span className="led"></span><span><b>QuickBooks</b> {converted ? "invoice queued" : "synced"}</span><span className="t">{converted ? "just now" : "2 min ago"}</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ----- Catalog page: live search + selectable rows ----- */
function CatalogPage({ sel, setSel, onAddToQuote }) {
  const [q, setQ] = useState("");
  const rows = useMemo(() => partRows.filter((p) =>
    !q || (p.pn + " " + p.desc + " " + p.cat).toLowerCase().includes(q.toLowerCase())), [q]);
  const cur = partRows.find((p) => p.pn === sel) || partRows[0];
  return (
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
            <span className="srch" style={{ flex: 1, minWidth: 220 }}>
              <span className="ic">{I.search}</span>
              <input placeholder="Search 4,182 parts… number, description, cross-ref" value={q} onChange={(e) => setQ(e.target.value)} style={{ color: "var(--ink)" }} />
              <span className="kbd">/</span>
            </span>
            <span className="fchip on">All</span><span className="fchip">Fuel</span><span className="fchip">Air</span>
            <span className="fchip">Cooling</span><span className="fchip">Low stock</span><span className="fchip">Cores due</span>
          </div>
          <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div style={{ flex: 1, overflow: "auto" }}>
              <table className="tbl">
                <thead><tr>
                  <th>Part #</th><th>Description</th><th>Bin</th>
                  <th className="r">On hand</th><th className="r">Comm.</th><th className="r">Avail</th>
                  <th className="r">Core</th><th className="r">List</th><th>Status</th>
                </tr></thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.pn} className={p.pn === cur.pn ? "sel" : ""} onClick={() => setSel(p.pn)} style={{ cursor: "pointer" }}>
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
                  {rows.length === 0 && <tr><td colSpan="9" style={{ textAlign: "center", padding: 28, color: "var(--steel-400)" }}>No parts match “{q}”</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="pgr"><span>{q ? rows.length + " of 4,182 parts" : "4,182 parts"}</span><span style={{ marginLeft: "auto" }}></span><span className="pg on">1</span><span className="pg">2</span><span className="pg">3</span><span>…</span><span className="pg">381</span></div>
          </div>
        </div>
        <div className="drawer">
          <div className="card">
            <div className="chd"><h4>Part Detail</h4><Chip kind={cur.st}>{cur.st === "ok" ? "In stock" : cur.st === "low" ? "Low" : "Out"}</Chip></div>
            <div style={{ padding: "12px 14px 6px" }}>
              <div className="bigpn">{cur.pn}</div>
              <div style={{ fontSize: 12.5, color: "var(--steel-600)", marginTop: 3, lineHeight: 1.5 }}>{cur.desc}<br /><span style={{ color: "var(--steel-400)" }}>{cur.cat} · Bin {cur.bin}</span></div>
            </div>
            <div style={{ padding: "6px 0 4px" }}>
              <div className="kv"><span>List</span><b className="money">${cur.list}</b></div>
              <div className="kv"><span>Cost (avg)</span><span className="money">${cur.cost}</span></div>
              <div className="kv"><span>Core charge</span><span className="money" style={{ color: "#b06511" }}>{cur.core === "—" ? "none" : "$" + cur.core}</span></div>
            </div>
          </div>
          <div className="card">
            <div className="chd"><h4>Availability</h4><span className="r">{cur.oh} on hand</span></div>
            <div style={{ padding: "4px 0" }}>
              <div className="kv"><span>Committed</span><span className="money">{cur.com}</span></div>
              <div className="kv"><span>Available</span><b className="money">{cur.av}</b></div>
            </div>
          </div>
          <span onClick={onAddToQuote} style={{ cursor: "pointer", display: "contents" }}><Btn kind="pri" ic={I.plus}>Add to Quote</Btn></span>
        </div>
      </div>
    </div>
  );
}

/* ----- Dashboard page ----- */
function DashPage({ onOpenQuote }) {
  return (
    <div className="ws">
      <div className="tb">
        <Crumb items={["Dashboard"]} />
        <span className="grow"></span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--steel-400)" }}>Thu Jun 11 · Front Counter</span>
      </div>
      <DashBody onOpenQuote={onOpenQuote} />
    </div>
  );
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [page, setPage] = useState("dash");
  const [converted, setConverted] = useState(false);
  const [qtys, setQtys] = useState({});
  const [sel, setSel] = useState("10R-1000");
  const dense = t.density === "compact";
  return (
    <div style={{ position: "fixed", inset: 0 }}>
      <style>{dense ? ".tbl td{padding:5px 10px;} .kv{padding:4px 14px;}" : ""}</style>
      <div className={"erp " + t.chrome} data-screen-label={"Axle app · " + page} style={{ height: "100%" }}>
        <AppSidebar chrome={t.chrome} active={page} onNav={setPage} />
        {page === "dash" && <DashPage onOpenQuote={() => setPage("quote")} />}
        {page === "quote" && <QuotePage converted={converted} setConverted={setConverted} qtys={qtys} setQtys={setQtys} showMargin={t.showMargin} />}
        {page === "inventory" && <CatalogPage sel={sel} setSel={setSel} onAddToQuote={() => setPage("quote")} />}
      </div>
      <TweaksPanel>
        <TweakSection label="Chrome" />
        <TweakRadio label="Sidebar" value={t.chrome} options={["dark", "light"]} onChange={(v) => setTweak("chrome", v)} />
        <TweakSection label="Tables" />
        <TweakRadio label="Density" value={t.density} options={["regular", "compact"]} onChange={(v) => setTweak("density", v)} />
        <TweakToggle label="Margin bar (admin)" value={t.showMargin} onChange={(v) => setTweak("showMargin", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
