// DOM regression checks. These do not substitute for real-browser visual testing.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

export async function runFrontendTests(parseHTML) {
  const root = new URL("../algotrading/web/static/", import.meta.url);
  const { document, window } = parseHTML(await readFile(new URL("index.html", root), "utf8"));
  const context = vm.createContext({ document, window, Intl, Map, Set, Date, Number, String,
    Array, Math, Object, JSON, Promise, setTimeout, clearTimeout, console, performance,
    innerWidth: 1000, innerHeight: 800,
    getComputedStyle: () => ({ getPropertyValue: () => "#77e3ac" }),
  });
  const source = await readFile(new URL("app.js", root), "utf8");
  new vm.Script(source.replace(/init\(\);\s*$/, "")).runInContext(context);
  new vm.Script(await readFile(new URL("lucide.min.js", root), "utf8")).runInContext(context);
  window.lucide = context.lucide;
  const ids = [...document.querySelectorAll("[id]")].map(el => el.id);
  assert.equal(new Set(ids).size, ids.length, "HTML IDs must be unique");
  const cashInput = document.querySelector('#backtest-form input[name="cash"]');
  assert.equal(cashInput.getAttribute("step"), "any", "Starting balance must not be constrained to offset increments");
  assert.ok(Number(cashInput.getAttribute("min")) < 0.01);
  const invalidIcons = [...document.querySelectorAll("[data-lucide]")].map(el => el.getAttribute("data-lucide"))
    .filter(name => !(name.split("-").map(word => word[0].toUpperCase() + word.slice(1)).join("") in context.lucide.icons));
  assert.deepEqual(invalidIcons, [], "All icons must exist in the vendored library");
  for (const match of source.matchAll(/on\("(#[a-zA-Z0-9-]+)"/g)) {
    assert.ok(document.querySelector(match[1]), "Event target missing: " + match[1]);
  }
  assert.equal(vm.runInContext("JSON.stringify([0,10,11,14,15,100].map(adaptivePageSize))", context), "[10,10,11,14,10,10]");
  assert.equal(vm.runInContext("pct(null)", context), "-");
  assert.equal(vm.runInContext("esc('<img onerror=bad>')", context), "&lt;img onerror=bad&gt;");
  vm.runInContext("state.holdings=Array.from({length:14},(_,i)=>({symbol:'TEST'+i,shares:1,average_cost:10,market_value:10+i}));renderHoldings();", context);
  assert.equal(document.querySelectorAll("#holdings-table tr").length, 14);
  assert.equal(document.querySelector("#holdings-table tr td").textContent, "TEST13");
  for (const target of ["portfolio-value-chart", "backtest-value-chart", "report-chart"]) {
    const element = document.getElementById(target);
    for (const width of [360, 700, 1200]) {
      Object.defineProperty(element, "clientWidth", { value: width, configurable: true });
      Object.defineProperty(element, "clientHeight", { value: 300, configurable: true });
      vm.runInContext("renderPortfolioChart(Array.from({length:30},(_,i)=>({date:'2024-01-'+String(i+1).padStart(2,'0'),cash:100,total_value:1000+i*2,market_value:900+i*2})),[],'#" + target + "');", context);
      assert.equal(element.querySelectorAll("polyline").length, 1);
      assert.equal(element.querySelectorAll(".chart-label").length, 9, "Four Y ticks and five date ticks");
      assert.ok(!element.innerHTML.includes("NaN"));
    }
  }
  vm.runInContext("decorate()", context);
  assert.ok(document.querySelectorAll(".nav-tab svg").length === 5);
  return "Frontend DOM checks passed: icons, hooks, escaping, pagination, sorting, and chart geometry at 360/700/1200px.";
}

if (typeof process !== "undefined" && process.argv[1] && import.meta.url === new URL(process.argv[1], "file://").href) {
  const { parseHTML } = await import("linkedom");
  console.log(await runFrontendTests(parseHTML));
}
