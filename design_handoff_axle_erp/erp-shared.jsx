/* Axle ERP — shared chrome: icons, hub mark, sidebar, topbar, small bits. */

const I = {
  dash: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>,
  quote: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h4"/></svg>,
  inv: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7l9-4 9 4-9 4z"/><path d="M3 7v10l9 4 9-4V7"/></svg>,
  core: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 12a9 9 0 0115.5-6.2M21 12a9 9 0 01-15.5 6.2"/><path d="M18 3v3h-3M6 21v-3h3"/></svg>,
  cust: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="9" cy="7" r="4"/><path d="M3 21c0-4 3-6 6-6s6 2 6 6M16 3.5a4 4 0 010 7M21 21c0-3-1.5-5-4-5.5"/></svg>,
  po: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="9" cy="21" r="1.5"/><circle cx="19" cy="21" r="1.5"/><path d="M2 3h3l3 13h11l2.5-9H7"/></svg>,
  search: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.5-4.5"/></svg>,
  print: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9V2h12v7M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>,
  plus: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"><path d="M12 5v14M5 12h14"/></svg>,
  save: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>,
  arrow: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12h14M13 6l6 6-6 6"/></svg>,
  scan: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7V5a2 2 0 012-2h2M17 3h2a2 2 0 012 2v2M21 17v2a2 2 0 01-2 2h-2M7 21H5a2 2 0 01-2-2v-2"/><path d="M7 12h10"/></svg>,
  warn: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>,
};

function Hub({ ring = "#fff", bore = "var(--amber-2)", simple }) {
  return (
    <svg viewBox="0 0 64 64" fill="none" stroke={ring} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" style={{ width: "100%", height: "100%", display: "block" }}>
      <circle cx="32" cy="32" r="28" />
      {!simple && <circle cx="32" cy="32" r="17" strokeWidth="3" />}
      <circle cx="32" cy="32" r={simple ? 9 : 6.5} fill={bore} stroke="none" />
      {!simple && <rect x="30" y="20" width="4" height="6" rx="1" fill={ring} stroke="none" />}
    </svg>
  );
}

/* Sidebar. chrome: 'dark' | 'light'. rail: icon-only tablet mode. */
function Sidebar({ chrome = "dark", active = "quotes", rail }) {
  const dark = chrome === "dark";
  const items = [
    ["dash", "Dashboard", I.dash],
    ["quotes", "Quotes", I.quote, "14"],
    ["inventory", "Inventory", I.inv],
    ["cores", "Cores", I.core, "3"],
    ["po", "Purchasing", I.po],
    ["cust", "Customers", I.cust],
  ];
  return (
    <aside className="sb">
      <div className="brand">
        <span className="mk"><Hub ring={dark ? "#fff" : "var(--mil)"} bore={dark ? "var(--amber-2)" : "var(--amber)"} simple={rail} /></span>
        {!rail && <span className="w">Axle<span className="s">Parts Counter</span></span>}
      </div>
      <nav>
        {items.map(([k, label, ic, bdg]) => (
          <a key={k} className={k === active ? "on" : ""}>
            <span className="ic">{ic}</span>
            {!rail && label}
            {!rail && bdg && <span className="bdg">{bdg}</span>}
          </a>
        ))}
      </nav>
      <div className="foot">{rail ? <b>SC</b> : <span>Shop · <b>Front Counter</b><br />Sandra C. · Admin</span>}</div>
    </aside>
  );
}

function Crumb({ items }) {
  return (
    <span className="crumb">
      {items.map((t, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span className="sep">/</span>}
          {i === items.length - 1 ? <b>{t}</b> : t}
        </React.Fragment>
      ))}
    </span>
  );
}

function Chip({ kind = "draft", children }) {
  return <span className={"chip " + kind}><span className="dot"></span>{children}</span>;
}

function Btn({ kind = "ghost", sm, ic, children }) {
  return <span className={"btn " + kind + (sm ? " sm" : "")}>{ic && <span className="ic">{ic}</span>}{children}</span>;
}

function Search({ ph, kbd, grow }) {
  return (
    <span className="srch" style={grow ? { flex: 1 } : null}>
      <span className="ic">{I.search}</span>
      <span className="ph">{ph}</span>
      {kbd && <span className="kbd">{kbd}</span>}
    </span>
  );
}

Object.assign(window, { I, Hub, Sidebar, Crumb, Chip, Btn, Search });
