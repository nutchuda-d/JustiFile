// Shared helpers for the JustiFile web pages. All data comes from Odoo through /api/justifile/*.
const API = "/api/justifile";

async function api(path, options = {}) {
  const init = { credentials: "same-origin", headers: {} };
  if (options.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(API + path, init);
  let data = {};
  try { data = await response.json(); } catch (e) { /* not JSON */ }
  if (!response.ok) {
    const error = new Error(data.error || "Request failed (" + response.status + ")");
    error.status = response.status;
    throw error;
  }
  return data;
}

async function odooLogin(db, login, password) {
  const response = await fetch("/web/session/authenticate", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", params: { db, login, password } }),
  });
  const data = await response.json();
  if (data.error || !data.result || !data.result.uid) throw new Error("Wrong username or password.");
  return data.result;
}

async function odooLogout() {
  await fetch("/web/session/destroy", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", params: {} }),
  });
}

const money = (n) => Number(n || 0).toLocaleString("en-US", { maximumFractionDigits: 2 });
const $ = (id) => document.getElementById(id);

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;   // textContent: never inject HTML
  if (className) node.className = className;
  return node;
}

function row(cells) {
  const tr = document.createElement("tr");
  cells.forEach((cell) => {
    const td = el("td", null, cell && cell.num ? "num" : "");
    if (cell instanceof Node) td.appendChild(cell);
    else td.textContent = cell && cell.num ? cell.text : (cell ?? "");
    tr.appendChild(td);
  });
  return tr;
}
const num = (n) => ({ num: true, text: money(n) });

function contractsTable(items) {
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["Date", "Project", "Contractor", "Method"].forEach((h) => head.appendChild(el("th", h)));
  head.appendChild(el("th", "Amount (THB)", "num"));
  table.appendChild(head);
  items.forEach((c) => table.appendChild(row([c.contract_date, c.name, c.contractor, c.method, num(c.amount)])));
  return table;
}
