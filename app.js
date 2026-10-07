/*
  App logic for the Journal Entry Practice Tool.
  You normally do not need to edit this file.
  Scenarios and accounts live in scenarios.js.
*/
(function () {
  "use strict";

  // ---------- Helpers ----------

  var numberFormat = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

  // All money math is done in whole cents to avoid rounding errors.
  function toCents(n) { return Math.round(n * 100); }
  function formatCents(c) { return numberFormat.format(c / 100); }

  // Returns a number, null for an empty box, or NaN for something unreadable.
  function parseAmount(str) {
    var cleaned = String(str).replace(/[,\s]/g, "");
    if (cleaned === "") return null;
    if (!/^\d*\.?\d*$/.test(cleaned) || cleaned === ".") return NaN;
    return Number(cleaned);
  }

  // Makes account names comparable: ignores case, extra spaces and dash/quote styles.
  function normalize(name) {
    return String(name)
      .trim()
      .toLowerCase()
      .replace(/[–—]/g, "-")
      .replace(/[‘’]/g, "'")
      .replace(/\s+/g, " ");
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === "className") node.className = attrs[key];
        else node.setAttribute(key, attrs[key]);
      });
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function cap(side) { return side === "debit" ? "Debit" : "Credit"; }

  // ---------- DOM references ----------

  var $ = function (id) { return document.getElementById(id); };
  var linesEl = $("lines");
  var addLineBtn = $("add-line");
  var checkBtn = $("check");
  var tryAgainBtn = $("try-again");
  var nextBtn = $("next");
  var feedbackEl = $("feedback");
  var formErrorEl = $("form-error");

  // ---------- Load data from scenarios.js ----------

  var warnings = [];
  var chart = (typeof CHART_OF_ACCOUNTS === "object" && CHART_OF_ACCOUNTS) || {};
  var scenarios = (typeof SCENARIOS !== "undefined" && Array.isArray(SCENARIOS)) ? SCENARIOS : [];

  if (scenarios.length === 0) {
    showFatal("No scenarios could be loaded. If you edited scenarios.js, check for a missing comma, quote or bracket near your change.");
    return;
  }

  // Build a lookup of every account name in the chart.
  var accountLookup = {};
  Object.keys(chart).forEach(function (group) {
    chart[group].forEach(function (name) { accountLookup[normalize(name)] = name; });
  });

  // Prepare each scenario: total up its answer and check it is valid.
  scenarios.forEach(function (s, i) {
    var preview = String(s.text || "");
    if (preview.length > 40) preview = preview.slice(0, 40) + "...";
    var label = "Scenario " + (i + 1) + (preview ? ' ("' + preview + '")' : "");
    var dr = 0, cr = 0;
    (s.answer || []).forEach(function (line) {
      var key = normalize(line.account || "");
      if (!accountLookup[key]) {
        chart.Other = chart.Other || [];
        chart.Other.push(line.account);
        accountLookup[key] = line.account;
        warnings.push(label + ' uses "' + line.account + '", which is not in the chart of accounts. It was added under "Other".');
      }
      dr += toCents(Number(line.debit) || 0);
      cr += toCents(Number(line.credit) || 0);
    });
    if (dr !== cr || dr === 0) {
      warnings.push(label + " has an answer where debits (" + formatCents(dr) + ") do not equal credits (" + formatCents(cr) + ").");
    }
    if (["Basic", "Intermediate", "Advanced"].indexOf(s.level) === -1) {
      warnings.push(label + ' has level "' + s.level + '". Use "Basic", "Intermediate" or "Advanced".');
    }
  });

  if (warnings.length) {
    var warnBox = $("load-warning");
    warnBox.appendChild(el("strong", null, "Note for the teacher: please check scenarios.js"));
    var ul = el("ul");
    warnings.forEach(function (w) { ul.appendChild(el("li", null, w)); });
    warnBox.appendChild(ul);
    warnBox.hidden = false;
  }

  // ---------- State ----------

  var state = {
    level: "All",
    shuffle: false,
    order: [],        // indexes into scenarios for the current round
    pos: 0,           // position within order
    review: false,    // true when retrying only the missed scenarios
    roundResults: {}, // scenario index -> true/false (first check in this round)
    toRetry: [],      // missed or skipped scenarios from the last round
    checked: false,
    firstResult: {},  // scenario index -> true/false (first check only)
    correct: 0,
    attempted: 0
  };

  // ---------- Entry lines ----------

  function buildAccountSelect() {
    var select = el("select", { className: "acct", "aria-label": "Account" });
    select.appendChild(el("option", { value: "" }, "Choose account..."));
    Object.keys(chart).forEach(function (group) {
      var og = el("optgroup", { label: group });
      chart[group].forEach(function (name) { og.appendChild(el("option", { value: name }, name)); });
      select.appendChild(og);
    });
    return select;
  }

  function setSide(line, side) {
    line.dataset.side = side;
    line.classList.toggle("is-credit", side === "credit");
    line.querySelectorAll(".side-btn").forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.side === side ? "true" : "false");
    });
  }

  function addLine(side) {
    var line = el("div", { className: "line" });

    var top = el("div", { className: "line-top" });
    top.appendChild(el("span", { className: "line-num", "aria-hidden": "true" }));
    top.appendChild(buildAccountSelect());

    var bottom = el("div", { className: "line-bottom" });
    var amt = el("input", {
      className: "amt",
      type: "text",
      inputmode: "decimal",
      placeholder: "0.00",
      autocomplete: "off",
      "aria-label": "Amount"
    });
    amt.addEventListener("input", updateTotals);
    amt.addEventListener("blur", function () {
      var n = parseAmount(amt.value);
      if (n !== null && !isNaN(n)) amt.value = formatCents(toCents(n));
    });
    amt.addEventListener("focus", function () {
      // Remove commas while typing so the number is easy to edit.
      amt.value = amt.value.replace(/,/g, "");
    });

    var toggle = el("div", { className: "side-toggle", role: "group", "aria-label": "Debit or credit" });
    ["debit", "credit"].forEach(function (s) {
      var b = el("button", { type: "button", className: "side-btn", "data-side": s }, cap(s));
      b.addEventListener("click", function () { setSide(line, s); updateTotals(); });
      toggle.appendChild(b);
    });

    var remove = el("button", { type: "button", className: "remove-btn", "aria-label": "Remove line" }, "×");
    remove.addEventListener("click", function () {
      line.remove();
      if (linesEl.children.length === 0) addLine("debit");
      renumber();
      updateTotals();
    });

    top.appendChild(remove);
    bottom.appendChild(amt);
    bottom.appendChild(toggle);
    line.appendChild(top);
    line.appendChild(bottom);
    linesEl.appendChild(line);

    setSide(line, side);
    renumber();
    return line;
  }

  function renumber() {
    Array.prototype.forEach.call(linesEl.children, function (line, i) {
      line.querySelector(".line-num").textContent = (i + 1) + ".";
    });
  }

  function readLines() {
    return Array.prototype.map.call(linesEl.children, function (line, i) {
      return {
        number: i + 1,
        account: line.querySelector(".acct").value,
        amount: parseAmount(line.querySelector(".amt").value),
        side: line.dataset.side
      };
    });
  }

  function sumSides(lines) {
    var t = { debit: 0, credit: 0 };
    lines.forEach(function (l) {
      if (typeof l.amount === "number" && !isNaN(l.amount)) t[l.side] += toCents(l.amount);
    });
    return t;
  }

  function updateTotals() {
    var t = sumSides(readLines());
    $("total-debit").textContent = formatCents(t.debit);
    $("total-credit").textContent = formatCents(t.credit);
    var status = $("balance-status");
    if (t.debit === 0 && t.credit === 0) {
      status.textContent = "";
      status.className = "balance-status";
    } else if (t.debit === t.credit) {
      status.textContent = "✓ Debits equal credits";
      status.className = "balance-status ok";
    } else {
      status.textContent = "Difference: " + formatCents(Math.abs(t.debit - t.credit));
      status.className = "balance-status off";
    }
  }

  function setLocked(locked) {
    linesEl.querySelectorAll("select, input, button").forEach(function (n) { n.disabled = locked; });
    addLineBtn.disabled = locked;
    checkBtn.disabled = locked;
  }

  function resetEntry() {
    linesEl.innerHTML = "";
    addLine("debit");
    addLine("credit");
    updateTotals();
    setLocked(false);
    state.checked = false;
    feedbackEl.hidden = true;
    formErrorEl.hidden = true;
  }

  // ---------- Scenario display ----------

  // Indexes of all scenarios in the selected level, in file order.
  function levelIndexes() {
    var list = [];
    scenarios.forEach(function (s, i) {
      if (state.level === "All" || s.level === state.level) list.push(i);
    });
    return list;
  }

  function shuffled(list) {
    var a = list.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  // A round is one pass through a list of scenarios, ending with a summary.
  function startRound(indexes, review) {
    state.order = state.shuffle ? shuffled(indexes) : indexes.slice();
    state.pos = 0;
    state.review = !!review;
    state.roundResults = {};
    setSummaryVisible(false);
    showScenario();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function currentIndex() { return state.order[state.pos]; }

  function showScenario() {
    var s = scenarios[currentIndex()];
    var total = state.order.length;
    $("scenario-level").textContent = s.level;
    $("scenario-progress").textContent = (state.review ? "Review " : "Scenario ") + (state.pos + 1) + " of " + total;
    $("scenario-text").textContent = s.text;
    var noteEl = $("scenario-note");
    noteEl.textContent = state.review ? "Review round: only the scenarios you missed or skipped." : "";
    noteEl.hidden = !state.review;
    nextBtn.textContent = state.pos === total - 1 ? "See results" : "Next scenario";
    resetEntry();
  }

  // ---------- End-of-round summary ----------

  function setSummaryVisible(visible) {
    $("summary").hidden = !visible;
    document.querySelector(".scenario").hidden = visible;
    document.querySelector(".entry").hidden = visible;
    $("actions").hidden = visible;
    if (visible) feedbackEl.hidden = true;
  }

  function showSummary() {
    var right = [], missed = [], skipped = [];
    state.order.forEach(function (i) {
      if (state.roundResults[i] === true) right.push(i);
      else if (state.roundResults[i] === false) missed.push(i);
      else skipped.push(i);
    });
    var total = state.order.length;

    $("summary-heading").textContent = right.length === total
      ? "\u2705 Perfect round!"
      : (state.review ? "Review complete" : "Round complete");
    $("summary-score").textContent = right.length + " of " + total + " correct on the first try";

    var detail = [];
    if (missed.length) detail.push(missed.length + " missed");
    if (skipped.length) detail.push(skipped.length + " skipped");
    $("summary-detail").textContent = detail.length
      ? detail.join(", ") + ". Retry them below, or start the whole set again."
      : "You answered every scenario correctly on your first try.";

    var list = $("summary-list");
    list.innerHTML = "";
    state.order.forEach(function (i) {
      var result = state.roundResults[i];
      var status = result === true ? "ok" : (result === false ? "bad" : "skip");
      var li = el("li", { className: status });
      li.appendChild(el("span", { className: "icon", "aria-hidden": "true" },
        status === "ok" ? "\u2713" : (status === "bad" ? "\u2717" : "\u2013")));
      var body = el("span", { className: "summary-text" });
      body.appendChild(el("span", { className: "sr-only" },
        status === "ok" ? "Correct: " : (status === "bad" ? "Missed: " : "Skipped: ")));
      body.appendChild(document.createTextNode(scenarios[i].text));
      body.appendChild(el("span", { className: "summary-level" }, scenarios[i].level));
      li.appendChild(body);
      list.appendChild(li);
    });

    var toRetry = missed.concat(skipped);
    var retryBtn = $("retry-missed");
    retryBtn.hidden = toRetry.length === 0;
    retryBtn.textContent = "Retry missed (" + toRetry.length + ")";
    $("start-over").className = "btn " + (toRetry.length ? "btn-secondary" : "btn-primary");

    state.toRetry = toRetry.sort(function (a, b) { return a - b; });
    setSummaryVisible(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------- Checking ----------

  // Groups lines by account and adds up debits and credits for each.
  function aggregate(lines) {
    var map = {};
    var order = [];
    lines.forEach(function (l) {
      var key = normalize(l.account);
      if (!map[key]) {
        map[key] = { name: accountLookup[key] || l.account, debit: 0, credit: 0 };
        order.push(key);
      }
      map[key].debit += l.debit;
      map[key].credit += l.credit;
    });
    return { map: map, order: order };
  }

  function describe(entry) {
    var parts = [];
    if (entry.debit) parts.push("Debit " + formatCents(entry.debit));
    if (entry.credit) parts.push("Credit " + formatCents(entry.credit));
    return parts.join(" and ");
  }

  function checkAnswer() {
    var raw = readLines();
    var errors = [];
    var used = [];

    raw.forEach(function (l) {
      var hasAcct = l.account !== "";
      var hasAmt = l.amount !== null;
      if (!hasAcct && !hasAmt) return; // blank line, ignore it
      if (!hasAcct) errors.push("Line " + l.number + ": choose an account.");
      else if (!hasAmt) errors.push("Line " + l.number + ": enter an amount.");
      else if (isNaN(l.amount) || l.amount <= 0) errors.push("Line " + l.number + ": the amount must be a number greater than zero.");
      else used.push(l);
    });

    if (errors.length === 0 && used.length < 2) {
      errors.push("A journal entry needs at least two lines: at least one debit and one credit.");
    }

    if (errors.length) {
      formErrorEl.innerHTML = "";
      var ul = el("ul");
      errors.forEach(function (e) { ul.appendChild(el("li", null, e)); });
      formErrorEl.appendChild(ul);
      formErrorEl.hidden = false;
      return;
    }
    formErrorEl.hidden = true;

    var idx = currentIndex();
    var scenario = scenarios[idx];

    var student = aggregate(used.map(function (l) {
      var c = toCents(l.amount);
      return { account: l.account, debit: l.side === "debit" ? c : 0, credit: l.side === "credit" ? c : 0 };
    }));
    var correct = aggregate((scenario.answer || []).map(function (a) {
      return { account: a.account, debit: toCents(Number(a.debit) || 0), credit: toCents(Number(a.credit) || 0) };
    }));

    var items = [];
    var totals = sumSides(used);
    var balanced = totals.debit === totals.credit;

    items.push(balanced
      ? { ok: true, text: "Your total debits (" + formatCents(totals.debit) + ") equal your total credits." }
      : { ok: false, text: "Your entry is not balanced: debits are " + formatCents(totals.debit) + " but credits are " + formatCents(totals.credit) + " (difference " + formatCents(Math.abs(totals.debit - totals.credit)) + ")." });

    correct.order.forEach(function (key) {
      var c = correct.map[key];
      var s = student.map[key];
      var side = c.debit ? "debit" : "credit";
      var other = side === "debit" ? "credit" : "debit";
      var amount = c[side];

      if (!s) {
        items.push({ ok: false, text: "Missing account: " + c.name + " should be a " + cap(side) + " of " + formatCents(amount) + "." });
      } else if (s.debit === c.debit && s.credit === c.credit) {
        items.push({ ok: true, text: c.name + ": " + describe(c) + ". Correct account, side and amount." });
      } else if (s[side] === 0) {
        var msg = c.name + " is the right account, but it belongs on the " + cap(side) + " side, not the " + cap(other) + " side.";
        if (s[other] !== amount) msg += " The amount should also be " + formatCents(amount) + ".";
        items.push({ ok: false, text: msg });
      } else if (s[other] !== 0) {
        items.push({ ok: false, text: c.name + " should appear only as a " + cap(side) + " of " + formatCents(amount) + ", but you put it on both sides." });
      } else {
        items.push({ ok: false, text: c.name + " is on the right side (" + cap(side) + "), but the amount should be " + formatCents(amount) + ", not " + formatCents(s[side]) + "." });
      }
    });

    student.order.forEach(function (key) {
      if (!correct.map[key]) {
        items.push({ ok: false, text: student.map[key].name + " does not belong in this entry (you entered " + describe(student.map[key]) + ")." });
      }
    });

    var allCorrect = items.every(function (it) { return it.ok; });

    // Score: only the first check of each scenario counts.
    var isFirst = !(idx in state.firstResult);
    if (isFirst) {
      state.firstResult[idx] = allCorrect;
      state.attempted += 1;
      if (allCorrect) state.correct += 1;
      updateScore();
    }

    // Round summary: only the first check in this round counts.
    if (!(idx in state.roundResults)) state.roundResults[idx] = allCorrect;

    renderFeedback(scenario, correct, items, allCorrect, isFirst);
    state.checked = true;
    setLocked(true);
  }

  function renderFeedback(scenario, correct, items, allCorrect, isFirst) {
    feedbackEl.className = "card feedback " + (allCorrect ? "is-correct" : "is-wrong");
    $("result-headline").textContent = allCorrect
      ? "✅ Correct! Well done."
      : "❌ Not quite. See what to fix below.";
    $("practice-note").hidden = isFirst;

    var list = $("feedback-list");
    list.innerHTML = "";
    items.forEach(function (it) {
      var li = el("li", { className: it.ok ? "ok" : "bad" });
      li.appendChild(el("span", { className: "icon", "aria-hidden": "true" }, it.ok ? "✓" : "✗"));
      li.appendChild(el("span", null, it.text));
      list.appendChild(li);
    });

    // Correct entry table: debits first, then credits (indented).
    var body = $("answer-body");
    body.innerHTML = "";
    var keys = correct.order.slice().sort(function (a, b) {
      return (correct.map[a].debit ? 0 : 1) - (correct.map[b].debit ? 0 : 1);
    });
    var totalDr = 0, totalCr = 0;
    keys.forEach(function (key) {
      var c = correct.map[key];
      totalDr += c.debit;
      totalCr += c.credit;
      var tr = el("tr");
      tr.appendChild(el("td", { className: c.debit ? "" : "indent" }, c.name));
      tr.appendChild(el("td", { className: "num" }, c.debit ? formatCents(c.debit) : ""));
      tr.appendChild(el("td", { className: "num" }, c.credit ? formatCents(c.credit) : ""));
      body.appendChild(tr);
    });
    var table = body.parentNode;
    var oldFoot = table.querySelector("tfoot");
    if (oldFoot) oldFoot.remove();
    var tfoot = el("tfoot");
    var trTotal = el("tr");
    trTotal.appendChild(el("td", null, "Total"));
    trTotal.appendChild(el("td", { className: "num" }, formatCents(totalDr)));
    trTotal.appendChild(el("td", { className: "num" }, formatCents(totalCr)));
    tfoot.appendChild(trTotal);
    table.appendChild(tfoot);

    $("explanation").textContent = scenario.explanation || "";

    feedbackEl.hidden = false;
    feedbackEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function updateScore() {
    var text = state.correct + " / " + state.attempted;
    if (state.attempted > 0) text += " (" + Math.round((state.correct / state.attempted) * 100) + "%)";
    $("score-value").textContent = text;
  }

  // ---------- Fatal error ----------

  function showFatal(message) {
    var box = document.getElementById("load-warning");
    box.className = "notice notice-error";
    box.textContent = message;
    box.hidden = false;
    document.querySelectorAll(".levels, .card, .actions, .footer-links").forEach(function (n) { n.hidden = true; });
  }

  // ---------- Wire up buttons ----------

  addLineBtn.addEventListener("click", function () {
    var t = sumSides(readLines());
    var line = addLine(t.debit > t.credit ? "credit" : "debit");
    line.querySelector(".acct").focus();
  });

  checkBtn.addEventListener("click", checkAnswer);

  tryAgainBtn.addEventListener("click", function () {
    resetEntry();
    document.querySelector(".scenario").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  nextBtn.addEventListener("click", function () {
    if (state.pos >= state.order.length - 1) {
      showSummary();
      return;
    }
    state.pos += 1;
    showScenario();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  $("retry-missed").addEventListener("click", function () {
    startRound(state.toRetry, true);
  });

  $("start-over").addEventListener("click", function () {
    startRound(levelIndexes());
  });

  $("shuffle").addEventListener("change", function () {
    state.shuffle = this.checked;
    startRound(levelIndexes());
  });

  document.querySelectorAll(".level-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".level-btn").forEach(function (b) {
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      });
      state.level = btn.dataset.level;
      startRound(levelIndexes());
    });
  });

  $("reset-score").addEventListener("click", function () {
    if (!window.confirm("Reset your score to zero?")) return;
    state.firstResult = {};
    state.correct = 0;
    state.attempted = 0;
    updateScore();
  });

  // ---------- Start ----------

  // Disable level buttons that have no scenarios yet.
  document.querySelectorAll(".level-btn").forEach(function (btn) {
    var lvl = btn.dataset.level;
    btn.disabled = lvl !== "All" && !scenarios.some(function (s) { return s.level === lvl; });
  });

  startRound(levelIndexes());
  updateScore();
})();
