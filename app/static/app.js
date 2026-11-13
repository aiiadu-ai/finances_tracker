// A new entry defaults to today on this device, even if the server's clock is in another time zone.
const dateInput = document.querySelector("input[data-device-date]");
if (dateInput) {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  dateInput.value = local.toISOString().slice(0, 10);
}

// ---- Calculator for the amount ----
// Money is counted in whole kopiykas / cents, so 0.1 + 0.2 is exactly 0.3.
const MAX_CENTS = 99999999999; // same limit as the server

const OPS = "+-*/";
const SHOWN = { "+": "+", "-": "−", "*": "×", "/": "÷" };

function toCents(text) {
  const [whole, fraction = ""] = text.split(".");
  return Number(whole || "0") * 100 + Number(fraction.padEnd(2, "0"));
}

function fromCents(cents) {
  const whole = Math.floor(cents / 100);
  const rest = cents % 100;
  return rest ? `${whole}.${String(rest).padStart(2, "0")}` : String(whole);
}

// Returns {cents} or {error}. A dangling operator at the end ("5+") is ignored.
function evaluate(expression) {
  let text = expression;
  while (text && OPS.includes(text.at(-1))) text = text.slice(0, -1);
  if (!text) return { error: "Enter an amount." };
  const parts = text.split(/([+\-*/])/); // numbers at even places, operators at odd
  const numbers = parts.filter((_, i) => i % 2 === 0).map(toCents);
  const operators = parts.filter((_, i) => i % 2 === 1);

  // × and ÷ first, then + and −.
  const terms = [numbers[0]];
  const adds = [];
  for (let i = 0; i < operators.length; i++) {
    const next = numbers[i + 1];
    const last = terms.length - 1;
    if (operators[i] === "*") {
      terms[last] = Math.round((terms[last] * next) / 100);
    } else if (operators[i] === "/") {
      if (next === 0) return { error: "Can't divide by zero." };
      terms[last] = Math.round((terms[last] * 100) / next);
    } else {
      adds.push(operators[i]);
      terms.push(next);
    }
  }
  let total = terms[0];
  adds.forEach((op, i) => {
    total += op === "+" ? terms[i + 1] : -terms[i + 1];
  });
  if (Math.abs(total) > MAX_CENTS) return { error: "That's too large." };
  return { cents: total };
}

function setupCalculator(calc) {
  const form = calc.closest("form");
  const amountInput = form.querySelector("#amount");
  const exprBox = calc.querySelector("[data-expr]");
  const resultBox = calc.querySelector("[data-result]");
  const errorBox = calc.querySelector("[data-calc-error]");

  const typed = amountInput.value.replace(/\s/g, "").replace(",", ".");
  let expr = /^\d+(\.\d{1,2})?$/.test(typed) ? typed : "";
  let fresh = false; // true right after "=": the next digit starts a new number

  function showError(message) {
    errorBox.textContent = message ? `✗ ${message}` : "";
    errorBox.hidden = !message;
  }

  function render() {
    exprBox.replaceChildren();
    if (!expr) exprBox.append("0");
    for (const piece of expr.split(/([+\-*/])/)) {
      if (!piece) continue;
      if (OPS.includes(piece)) {
        const op = document.createElement("span");
        op.className = "op";
        op.textContent = SHOWN[piece];
        exprBox.append(op);
      } else {
        exprBox.append(piece);
      }
    }
    exprBox.dataset.size = expr.length > 16 ? "s" : expr.length > 9 ? "m" : "l";

    const hasOperator = [...expr].some((ch) => OPS.includes(ch));
    const result = hasOperator ? evaluate(expr) : null;
    resultBox.textContent = result && "cents" in result ? `= ${fromCents(result.cents)}` : "";
  }

  function press(key) {
    showError("");
    const isNumberKey = /^[0-9]$/.test(key) || key === ".";
    if (fresh && isNumberKey) expr = ""; // after "=", typing a number starts over
    fresh = false;
    const current = expr.split(/[+\-*/]/).at(-1);
    if (/^[0-9]$/.test(key)) {
      if (current.includes(".") && current.split(".")[1].length >= 2) return;
      if (current === "0") expr = expr.slice(0, -1); // no "007"
      expr += key;
    } else if (key === ".") {
      if (current.includes(".")) return;
      expr += current === "" ? "0." : ".";
    } else if (OPS.includes(key)) {
      if (!expr) return;
      expr = OPS.includes(expr.at(-1)) ? expr.slice(0, -1) + key : expr + key;
    } else if (key === "Backspace") {
      expr = expr.slice(0, -1);
    } else if (key === "=") {
      if (!expr) return;
      const result = evaluate(expr);
      if ("error" in result) return showError(result.error);
      if (result.cents <= 0) return showError("Enter an amount above zero.");
      expr = fromCents(result.cents);
      fresh = true;
    }
    render();
  }

  calc.querySelectorAll("[data-key]").forEach((button) => {
    // A mouse click must not leave focus on the key, or Enter would press that key again.
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => press(button.dataset.key));
  });

  // On a computer the keyboard works too (but not while typing in the comment or date box).
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target.closest("input[type=text], input[type=date], textarea")) return;
    if (target.closest("button, a") && (event.key === "Enter" || event.key === " ")) return;
    let key = event.key;
    if (key === ",") key = ".";
    if (key === "Enter") key = "=";
    if (/^[0-9]$/.test(key) || key === "." || OPS.includes(key) || key === "Backspace" || key === "=") {
      event.preventDefault();
      press(key);
    }
  });

  // On save, work out the expression and send only the final amount.
  form.addEventListener("submit", (event) => {
    const result = expr ? evaluate(expr) : { error: "Enter an amount." };
    if ("error" in result || result.cents <= 0) {
      event.preventDefault();
      showError("error" in result ? result.error : "Enter an amount above zero.");
      return;
    }
    amountInput.value = fromCents(result.cents);
  });

  render();
}

document.querySelectorAll("[data-calc]").forEach(setupCalculator);

// ---- Theme: Light / Dark / System, remembered in this browser ----
function setupThemeSwitch() {
  const buttons = document.querySelectorAll("[data-theme-choice]");
  let current = "system";
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") current = saved;
  } catch (e) { /* storage blocked: the choice just won't be remembered */ }

  function show() {
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === current)));
  }
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      current = button.dataset.themeChoice;
      if (current === "system") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = current;
      try {
        if (current === "system") localStorage.removeItem("theme");
        else localStorage.setItem("theme", current);
      } catch (e) { /* see above */ }
      show();
    });
  });
  show();
}
setupThemeSwitch();

// ---- Home: one currency at a time (₴ or €), remembered in this browser ----
function setupCurrencySwitch() {
  const buttons = document.querySelectorAll("[data-cur-choice]");
  if (!buttons.length) return;
  function show() {
    const current = document.documentElement.dataset.cur || "UAH";
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.curChoice === current)));
  }
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      document.documentElement.dataset.cur = button.dataset.curChoice;
      try { localStorage.setItem("currency", button.dataset.curChoice); } catch (e) { /* not remembered */ }
      show();
      fitNumbers();
    });
  });
  show();
}

// ---- Big numbers stay on one line: shrink the font a little until the amount fits ----
function fitNumbers() {
  document.querySelectorAll("[data-fit]").forEach((el) => {
    el.style.fontSize = "";
    if (!el.clientWidth) return; // hidden (the other currency): fitted when it is shown
    let size = parseFloat(getComputedStyle(el).fontSize);
    const smallest = size * 0.5;
    while (el.scrollWidth > el.clientWidth && size > smallest) {
      size -= 1;
      el.style.fontSize = size + "px";
    }
  });
}

setupCurrencySwitch();
fitNumbers();
window.addEventListener("resize", fitNumbers);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitNumbers);

// ---- Year: Plan / Actual / Difference, and "All months" fill ----
function setupYear() {
  const grid = document.querySelector(".yr");
  if (!grid) return;
  const buttons = document.querySelectorAll("[data-mode-choice]");
  function setMode(mode) {
    grid.dataset.mode = mode;
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.modeChoice === mode)));
    fitNumbers();
  }
  try {
    const saved = localStorage.getItem("yearMode");
    if (saved === "actual" || saved === "diff") setMode(saved);
  } catch (e) { /* not remembered */ }
  buttons.forEach((button) => button.addEventListener("click", () => {
    setMode(button.dataset.modeChoice);
    try { localStorage.setItem("yearMode", button.dataset.modeChoice); } catch (e) { /* not remembered */ }
  }));

  // Typing one amount in "All months" fills the 12 plan boxes of that row.
  document.querySelectorAll("input[data-fill]").forEach((box) => {
    box.addEventListener("change", () => {
      if (!box.value.trim()) return;
      box.closest("tr").querySelectorAll("input.c-plan").forEach((cell) => { cell.value = box.value.trim(); });
      box.value = "";
    });
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); box.dispatchEvent(new Event("change")); }
    });
  });
}
setupYear();

// ---- Analytics: plan chart for expenses or income ----
document.querySelectorAll(".pa").forEach((box) => {
  const buttons = box.querySelectorAll("[data-pa-choice]");
  buttons.forEach((button) => button.addEventListener("click", () => {
    box.dataset.kind = button.dataset.paChoice;
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  }));
});

// ---- Analytics: tap a group column to open its categories; tap again or "back" to close ----
document.querySelectorAll(".wm").forEach((box) => {
  const buttons = box.querySelectorAll("[data-wm-group]");
  const details = box.querySelectorAll("[data-wm-detail]");
  function open(key) {
    if (key) box.dataset.open = key; else delete box.dataset.open;
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.wmGroup === key)));
    details.forEach((d) => d.classList.toggle("open", d.dataset.wmDetail === key));
  }
  buttons.forEach((button) => button.addEventListener("click", () => {
    open(box.dataset.open === button.dataset.wmGroup ? null : button.dataset.wmGroup);
  }));
  box.querySelectorAll("[data-wm-back]").forEach((b) => b.addEventListener("click", () => open(null)));
});

// ---- Add: "paid in ₴" only when adding money to a euro jar ----
(function setupJarFields() {
  const paid = document.querySelector("[data-paid]");
  if (!paid) return;
  const form = paid.closest("form");
  function show() {
    const jar = form.querySelector("input[name=jar_id]:checked");
    const dir = form.querySelector("input[name=direction]:checked");
    paid.hidden = !(jar && jar.dataset.cur === "EUR" && dir && dir.value === "in");
  }
  form.querySelectorAll("input[name=jar_id], input[name=direction]").forEach((i) => i.addEventListener("change", show));
  show();
})();

// ---- Jar form: the rate box only for euro jars ----
(function setupJarRate() {
  const rate = document.querySelector(".jar-rate");
  const radios = document.querySelectorAll("input[name=currency]");
  if (!rate || !radios.length || rate.closest("form").querySelector("[name=name]") === null) return;
  const isJarForm = rate.closest(".settings-form");
  if (!isJarForm) return;
  function show() {
    const picked = document.querySelector(".settings-form input[name=currency]:checked");
    rate.hidden = !!picked && picked.value !== "EUR";
  }
  radios.forEach((r) => r.addEventListener("change", show));
  show();
})();

// ---- Tabs (Settings, Home, Analytics): one block at a time ----
// The address (#rules, #categories ...) opens that tab. Home and Analytics (data-tabs-key) also remember the last tab
// and keep it when the month, the year or the currency link is followed.
(function setupTabs() {
  const bar = document.querySelector(".tabs[data-open]");
  if (!bar) return;
  const key = bar.dataset.tabsKey || "";
  const links = [...bar.querySelectorAll("[data-tab]")];
  const panels = [...document.querySelectorAll("[data-panel]")];
  // With JavaScript the tabs do the moving; without ids the browser does not scroll down to #rules.
  panels.forEach((p) => p.removeAttribute("id"));

  function remembered() {
    try { return key ? localStorage.getItem("tab:" + key) : null; } catch (e) { return null; }
  }
  function remember(name) {
    try { if (key) localStorage.setItem("tab:" + key, name); } catch (e) { /* not remembered */ }
  }
  function carry(name) {
    document.querySelectorAll("[data-carry-tab] a[href]").forEach((a) => {
      a.setAttribute("href", a.getAttribute("href").split("#")[0] + "#" + name);
    });
  }
  function show(name) {
    if (!links.some((l) => l.dataset.tab === name)) name = links[0].dataset.tab;
    panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
    links.forEach((l) => l.setAttribute("aria-current", l.dataset.tab === name ? "page" : "false"));
    remember(name);
    if (key) carry(name);
    fitNumbers(); // big numbers could not be fitted while their block was hidden
    return name;
  }
  links.forEach((l) => l.addEventListener("click", (event) => {
    event.preventDefault();
    history.replaceState(null, "", "#" + l.dataset.tab);
    show(l.dataset.tab);
  }));
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));

  const fromAddress = location.hash.slice(1);
  const shown = show(fromAddress || remembered() || bar.dataset.open);
  if (key && !fromAddress) history.replaceState(null, "", "#" + shown); // the tab is always in the address
})();

// ---- Installable app: lets the phone offer "Install app" / "Add to Home Screen" as a real app ----
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* not available here: the site still works */ });
}
