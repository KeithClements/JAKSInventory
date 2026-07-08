/* Axle ERP — Quote / Invoice builder @1280. chrome: 'dark' | 'light'. */

const quoteLines = [
  { n: 1, pn: "10R-1000", desc: "Reman Injector — CAT C15 Acert", bin: "A-04-2", qty: 4, unit: "615.00", core: "100.00", ext: "2,460.00" },
  { n: 2, pn: "4089826", desc: "Water Pump — Cummins ISX", bin: "B-11-4", qty: 1, unit: "289.50", core: "—", ext: "289.50" },
  { n: 3, pn: "23538522", desc: "EGR Cooler — Detroit S60 14L", bin: "C-02-1", qty: 1, unit: "1,184.00", core: "250.00", ext: "1,184.00", hot: true },
  { n: 4, pn: "1830560C92", desc: "Turbocharger — Navistar DT466E", bin: "A-09-3", qty: 1, unit: "947.25", core: "175.00", ext: "947.25" },
  { n: 5, pn: "BOS-0445120007", desc: "Common-Rail Injector — Bosch", bin: "D-01-6", qty: 2, unit: "318.00", core: "60.00", ext: "636.00" },
];

function QuoteScreen({ chrome = "dark" }) {
  return (
    <div className={"erp " + chrome} data-screen-label={"Quote builder · " + chrome + " chrome"}>
      <Sidebar chrome={chrome} active="quotes" />
      <div className="ws">
        <div className="tb">
          <Crumb items={["Quotes", "Q-2241"]} />
          <Chip kind="draft">Draft</Chip>
          <span className="grow"></span>
          <Btn ic={I.print}>Print</Btn>
          <Btn ic={I.save}>Save</Btn>
          <Btn kind="amb" ic={I.arrow}>Convert to Invoice</Btn>
        </div>
        <div className="body">
          {/* ===== line items ===== */}
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", flex: "0 0 auto" }}><Search ph="Add part — search by number, description, or scan…" kbd="/" grow /></div>
            <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
              <div className="chd">
                <h4>Line Items</h4>
                <span className="r">5 lines · 9 units</span>
              </div>
              <div style={{ flex: 1, overflow: "hidden" }}>
                <table className="tbl">
                  <thead><tr>
                    <th style={{ width: 28 }}>#</th><th>Part</th><th>Bin</th>
                    <th style={{ width: 92 }}>Qty</th><th className="r">Unit</th>
                    <th className="r">Core</th><th className="r">Ext</th>
                  </tr></thead>
                  <tbody>
                    {quoteLines.map((l) => (
                      <tr key={l.n} className={l.hot ? "hot" : ""}>
                        <td className="mono" style={{ color: "var(--steel-300)" }}>{l.n}</td>
                        <td><div className="pn">{l.pn}</div><div className="desc">{l.desc}</div></td>
                        <td className="mono" style={{ color: "var(--steel-400)" }}>{l.bin}</td>
                        <td><span className="qty"><button className="pm">−</button><span>{l.qty}</span><button className="pm">+</button></span></td>
                        <td className="r money">{l.unit}</td>
                        <td className="r">{l.core === "—" ? <span className="money" style={{ color: "var(--steel-300)" }}>—</span> : <span className="corechip">+{l.core}</span>}</td>
                        <td className="r money ext">{l.ext}</td>
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
          {/* ===== right rail ===== */}
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
                <div className="kv"><span>Subtotal</span><span className="money">$5,516.75</span></div>
                <div className="kv"><span>Core charges</span><span className="money" style={{ color: "#b06511" }}>$945.00</span></div>
                <div className="kv"><span>Shop supplies</span><span className="money">$24.00</span></div>
                <div className="kv"><span>Tax</span><span className="money" style={{ color: "var(--steel-400)" }}>exempt</span></div>
              </div>
              <div className="tot"><span className="l">Quote Total</span><span className="v">$6,485.75</span></div>
              <div className="margin">
                <div className="row"><span>Margin</span><b>27.4%</b></div>
                <div className="bar"><i style={{ width: "62%" }}></i></div>
              </div>
            </div>
            <div className="card">
              <div className="sync"><span className="led"></span><span><b>QuickBooks</b> synced</span><span className="t">2 min ago</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { QuoteScreen, quoteLines });
