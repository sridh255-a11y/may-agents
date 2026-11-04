(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var DEMO = [12, 18, 15, 24, 21, 30, 27, 36, 33, 41];
  var PREFIXES = ["Research: ", "Create: ", "Analyze: ", "Plan: "];
  var DEFAULT_PROJECT = "YouTube RankPeak";

  var state = {
    project: null, agents: [], status: null, tasks: [], pending: [],
    currentTask: null, currentTaskId: null, noLogin: false, checked: false,
    flash: null, open: {}, sig: {},
    pollTimer: null, taskTimer: null, flashTimer: null, started: false
  };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }
  function clip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "\u2026" : s; }
  function fmtTime(sec) { return new Date(sec * 1000).toLocaleTimeString(); }
  function isActive(t) { return t && (t.status === "queued" || t.status === "running"); }
  function reduced() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  function toast(msg, kind) {
    var box = $("toasts");
    while (box.children.length >= 4) { box.removeChild(box.firstChild); }
    var t = el("div", "toast " + (kind || ""), msg);
    box.appendChild(t);
    setTimeout(function () { if (t.parentNode) { t.parentNode.removeChild(t); } }, kind === "err" ? 6500 : 4000);
  }

  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (method === "POST") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body || {});
    }
    return fetch(path, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.status === 401 && path !== "/api/login") { needLogin(); }
        if (!res.ok) {
          var err = new Error((data && data.error) || ("Request failed (" + res.status + ")"));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function stopTimers() {
    clearInterval(state.pollTimer);
    clearTimeout(state.taskTimer);
    state.pollTimer = null;
    state.taskTimer = null;
    state.started = false;
  }

  function needLogin() {
    stopTimers();
    $("login").hidden = false;
    $("password").value = "";
    setTimeout(function () { $("password").focus(); }, 50);
  }

  function resetData() {
    state.status = null; state.tasks = []; state.pending = []; state.agents = [];
    state.currentTask = null; state.currentTaskId = null; state.sig = {};
    $("result").hidden = true;
    renderAll();
  }

  function openDialog(d) {
    if (d.showModal) { if (!d.open) { d.showModal(); } } else { d.setAttribute("open", ""); }
  }
  function closeDialog(d) {
    if (d.close) { if (d.open) { d.close(); } } else { d.removeAttribute("open"); }
  }

  function confirmAsk(title, text, yesLabel, noLabel) {
    return new Promise(function (resolve) {
      var d = $("confirm");
      $("confirm-title").textContent = title;
      $("confirm-text").textContent = text;
      $("confirm-yes").textContent = yesLabel;
      $("confirm-no").textContent = noLabel;
      var done = function (val) { d.oncancel = null; closeDialog(d); resolve(val); };
      $("confirm-yes").onclick = function () { done(true); };
      $("confirm-no").onclick = function () { done(false); };
      d.oncancel = function (e) { e.preventDefault(); done(false); };
      openDialog(d);
    });
  }

  function tickClock() {
    var d = new Date(), h = d.getHours();
    var g = h < 12 ? "Good morning" : (h < 18 ? "Good afternoon" : "Good evening");
    $("greeting").textContent = g + ", Sridhar";
    $("clock").textContent = d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) + "   |   " + d.toLocaleTimeString();
  }

  function runningCount() { return state.tasks.filter(isActive).length; }
  function projectTasks() { return state.tasks.filter(function (t) { return t.project === state.project; }); }

  function orbState() {
    if (isActive(state.currentTask) || runningCount() > 0) { return "processing"; }
    if (state.flash) { return state.flash; }
    if (state.checked && (!state.status || state.status.server !== "ok" || !state.status.model_link)) { return "disconnected"; }
    return "idle";
  }

  function updateOrb() {
    var s = orbState();
    $("orb").setAttribute("data-state", s);
    var label = { idle: "System ready", processing: "Processing", success: "Done", error: "Task failed" }[s];
    if (!state.checked && s === "idle") { label = "Checking connection"; }
    if (s === "disconnected") { label = (state.status && state.status.server === "ok") ? "Model link down" : "Server not reachable"; }
    $("orb-label").textContent = label;
  }

  function flash(kind, ms) {
    state.flash = kind;
    clearTimeout(state.flashTimer);
    state.flashTimer = setTimeout(function () { state.flash = null; updateOrb(); }, ms);
    updateOrb();
  }

  function updateConn() {
    if (!state.checked) { return; }
    var dot = $("conn-dot"), txt = $("conn-text");
    dot.className = "dot";
    if (!state.status) { dot.classList.add("bad"); txt.textContent = "Server not reachable"; }
    else if (!state.status.model_link) { txt.textContent = "Server up, model link down"; }
    else { dot.classList.add("ok"); txt.textContent = "Connected (127.0.0.1)"; }
  }

  function verdictOf(v) {
    var t = (v && typeof v === "object") ? v.verdict : v;
    t = String(t || "").trim().toUpperCase();
    if (t.indexOf("APPROVE") === 0) { return "APPROVE"; }
    if (t.indexOf("REJECT") === 0) { return "REJECT"; }
    if (t.indexOf("PLAN") === 0) { return "PLAN"; }
    return "UNCLEAR";
  }
  function verdictBadge(v) {
    var k = verdictOf(v);
    return el("span", "verdict " + k.toLowerCase(), k === "UNCLEAR" ? "NO CLEAR VERDICT" : k);
  }

  function fmtDur(s) {
    if (typeof s !== "number") { return "length ?"; }
    var m = Math.floor(s / 60), r = s % 60;
    return m + ":" + (r < 10 ? "0" : "") + r;
  }

  function trendsRun(ev) {
    ev.preventDefault();
    var topic = $("tr-topic").value.trim();
    if (!topic) { toast("Type a topic first.", "warn"); return; }
    var out = $("tr-out"), btn = $("tr-go");
    btn.disabled = true;
    clear(out);
    out.appendChild(el("p", "muted small", "Searching YouTube. This can take a few seconds."));
    api("POST", "/api/trends", { project: state.project, topic: topic, region: $("tr-region").value.trim(), days: parseInt($("tr-days").value, 10), length: $("tr-length").value }).then(function (r) {
      var t = r.trends || {};
      var s = t.summary || {};
      clear(out);
      if (t.message) { out.appendChild(el("p", "muted small", t.message)); return; }
      out.appendChild(el("p", "kv", "Summary"));
      out.appendChild(el("p", "", s.videos_analyzed + " videos | median " + s.median_views + " views | " + s.over_100k + " over 100k | " + s.small_channel_wins + " small-channel wins"));
      var p = t.patterns || {};
      out.appendChild(el("p", "muted small", "Titles: average " + p.avg_length + " characters, " + p.with_number_pct + "% use a number, " + p.question_pct + "% ask a question."));
      (t.videos || []).forEach(function (v, i) {
        var box = el("div", "claim" + (v.small_win ? "" : " nf"));
        var a = document.createElement("a");
        a.href = "https://www.youtube.com/watch?v=" + encodeURIComponent(v.id || "");
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = (i + 1) + ". " + v.title;
        box.appendChild(a);
        box.appendChild(el("small", "", " " + v.views + " views | " + fmtDur(v.seconds) + " | uploaded " + v.uploaded + " | subs " + v.subs + " | " + v.speed + "/day | outlier " + v.outlier + (v.small_win ? " | SMALL WIN" : "")));
        out.appendChild(box);
      });
    }).catch(function (e) { clear(out); toast(e.message, "err"); }).finally(function () { btn.disabled = false; });
  }

  document.addEventListener("submit", function (ev) {
    if (ev.target && ev.target.id === "tr-form") { trendsRun(ev); }
  });

  function seoBox(w) {
    var box = el("div", "");
    var out = el("div", "");
    var btn = el("button", "btn secondary", "Make SEO");
    btn.type = "button";
    btn.addEventListener("click", function () {
      btn.disabled = true;
      clear(out);
      out.appendChild(el("p", "muted small", "Working. This can take a minute on free models."));
      api("POST", "/api/seo", { project: state.project, script: w.script, title: w.title || "" }).then(function (r) {
        var s = r.seo || {};
        var c = s.checks || {};
        clear(out);
        out.appendChild(el("p", "kv", "SEO title (" + (c.title_len || 0) + " of 100)"));
        out.appendChild(el("p", "", s.title || ""));
        out.appendChild(el("p", "kv", "Description (" + (c.description_len || 0) + " of 5000)"));
        out.appendChild(el("pre", "script", s.description || ""));
        out.appendChild(el("p", "kv", "Tags (" + (c.tags_len || 0) + " of 500)"));
        out.appendChild(el("p", "", (s.tags || []).join(", ")));
        out.appendChild(el("p", "kv", "Hashtags"));
        out.appendChild(el("p", "", (s.hashtags || []).join(" ")));
        if (s.issues && s.issues.length) {
          out.appendChild(el("p", "kv", "Issues"));
          s.issues.forEach(function (i) { out.appendChild(el("div", "claim nf", i)); });
        } else {
          out.appendChild(el("p", "muted small", "SEO checks passed. Nothing is sent to YouTube."));
        }
      }).catch(function (e) { clear(out); toast(e.message, "err"); }).finally(function () { btn.disabled = false; });
    });
    box.appendChild(btn);
    box.appendChild(out);
    return box;
  }

  function writerNode(w) {
    var f = el("div", "");
    if (!w || typeof w !== "object") { return f; }
    if (w.title) { f.appendChild(el("p", "kv", "Title")); f.appendChild(el("p", "", w.title)); }
    if (w.script) { f.appendChild(el("p", "kv", "Script")); f.appendChild(el("pre", "script", w.script)); }
    if (Array.isArray(w.claims)) {
      f.appendChild(el("p", "kv", "Claims and sources (" + (w.verified || 0) + " verified, " + (w.not_found || 0) + " NOT FOUND)"));
      if (!w.claims.length) { f.appendChild(el("p", "muted small", "No claims listed.")); }
      w.claims.forEach(function (c) {
        var nf = String(c.source || "").toUpperCase() === "NOT FOUND";
        var box = el("div", "claim" + (nf ? " nf" : ""), c.claim || "");
        box.appendChild(el("small", "", (nf ? "NOT FOUND" : "FOUND in notes") + " | quote: " + (c.quote || "(none)")));
        f.appendChild(box);
      });
    }
    if (Array.isArray(w.claims) && typeof w.script === "string" && w.script.trim()) { f.appendChild(seoBox(w)); }
    return f;
  }

  function verdictExtras(v) {
    var f = el("div", "");
    if (!v || typeof v !== "object") { return f; }
    Object.keys(v).forEach(function (k) {
      if (k === "verdict") { return; }
      var val = v[k];
      if (Array.isArray(val)) {
        if (!val.length) { return; }
        f.appendChild(el("p", "kv", k));
        val.forEach(function (item) {
          f.appendChild(el("div", "claim nf", typeof item === "string" ? item : JSON.stringify(item)));
        });
      } else if (typeof val === "string" && val) {
        f.appendChild(el("p", "kv", k));
        f.appendChild(el("p", "", val));
      }
    });
    return f;
  }

  function renderResult(task) {
    var sec = $("result"), body = $("result-body");
    sec.hidden = false;
    clear(body);
    var r = task.result || {};
    $("result-status").textContent = String(task.status).toUpperCase();
    $("cancel-task").hidden = !isActive(task);
    if (isActive(task)) {
      body.appendChild(el("p", "muted", "Working. This can take a minute on free models."));
      body.appendChild(el("div", "skeleton"));
      body.appendChild(el("div", "skeleton"));
      body.appendChild(el("div", "skeleton short"));
      return;
    }
    if (task.status === "cancelled") {
      body.appendChild(el("p", "", "Cancelled. Nothing was added to the approval list."));
      return;
    }
    if (r.error) { body.appendChild(el("p", "error", r.error)); }
    if (r.message) { body.appendChild(el("p", "", r.message)); }
    if (r.route) {
      body.appendChild(el("p", "kv", "Manager route"));
      body.appendChild(el("p", "", r.route + (r.reason ? " - " + r.reason : "")));
    }
    if (r.checker_verdict) {
      body.appendChild(el("p", "kv", "Checker verdict"));
      var line = el("p", "");
      line.appendChild(verdictBadge(r.checker_verdict));
      body.appendChild(line);
      body.appendChild(verdictExtras(r.checker_verdict));
    }
    body.appendChild(writerNode(r.writer_result));
    if (r.gate_id !== null && r.gate_id !== undefined) {
      body.appendChild(el("p", "muted small", "Waiting in the approval list as #" + r.gate_id + ". Nothing is published or sent."));
    }
  }

  function finishTask(t) {
    var r = t.result || {};
    if (t.status === "done" && !r.error) {
      if (r.gate_id !== null && r.gate_id !== undefined) {
        toast("Done. Checker verdict: " + verdictOf(r.checker_verdict) + ". Waiting for your approval.", verdictOf(r.checker_verdict) === "APPROVE" ? "ok" : "warn");
        flash("success", 1800);
      } else if (r.message) {
        toast(r.message, "warn");
      }
    } else if (t.status === "error" || r.error) {
      toast(r.error || "The task failed.", "err");
      flash("error", 8000);
    } else if (t.status === "cancelled") {
      toast("Task cancelled.", "warn");
    }
    refresh();
  }

  function followTask(id) {
    clearTimeout(state.taskTimer);
    state.currentTaskId = id;
    var step = function () {
      api("GET", "/api/task?id=" + encodeURIComponent(id)).then(function (t) {
        if (state.currentTaskId !== id) { return; }
        var wasActive = isActive(state.currentTask);
        state.currentTask = t;
        if (t.project === state.project) { renderResult(t); }
        updateOrb();
        if (isActive(t)) { state.taskTimer = setTimeout(step, 1500); }
        else if (wasActive || t.status !== "done") { finishTask(t); }
        else { finishTask(t); }
      }).catch(function (e) {
        toast(e.message, "err");
        state.currentTask = null;
        updateOrb();
      });
    };
    step();
  }

  function submit(ev) {
    ev.preventDefault();
    var req = $("request").value.trim();
    if (!req) { toast("Type a request first.", "warn"); $("request").focus(); return; }
    var notes = $("notes").value;
    if (!notes.trim()) { toast("No source notes. The Writer has nothing to use and will say NOT FOUND.", "warn"); }
    var btn = $("send"), btn2 = $("send-plan");
    var mode = (ev.submitter && ev.submitter.id === "send-plan") ? "plan" : "run";
    if (btn2) { btn2.disabled = true; }
    btn.disabled = true;
    api("POST", "/api/submit", { project: state.project, request: req, notes: notes, mode: mode }).then(function (r) {
      toast("Task started.", "ok");
      $("request").value = "";
      followTask(r.task_id);
      refresh();
      $("result").scrollIntoView({ block: "nearest" });
    }).catch(function (e) {
      toast(e.message, "err");
    }).finally(function () { btn.disabled = false; if (btn2) { btn2.disabled = false; } });
  }

  function cancelTask() {
    if (!state.currentTaskId) { return; }
    var id = state.currentTaskId;
    api("POST", "/api/cancel", { id: id }).then(function () { followTask(id); refresh(); })
      .catch(function (e) { toast(e.message, "err"); });
  }

  function setBusy(card, busy) {
    card.querySelectorAll("button").forEach(function (b) { b.disabled = busy; });
  }

  function decide(item, action, card) {
    var v = verdictOf((item.payload || {}).checker_verdict);
    var go = function () {
      setBusy(card, true);
      api("POST", "/api/" + action, { id: item.id }).then(function () {
        toast(action === "approve" ? "Marked approved (#" + item.id + "). Nothing is published or sent yet." : "Rejected #" + item.id + ".", "ok");
        return refresh();
      }).catch(function (e) {
        toast(e.message, "err");
        setBusy(card, false);
        refresh();
      });
    };
    if (action === "reject") {
      confirmAsk("Reject this item?", "It will be marked rejected and leave the pending list.", "Reject", "Keep pending").then(function (y) { if (y) { go(); } });
    } else if (v !== "APPROVE" && v !== "PLAN") {
      confirmAsk("Approve anyway?", "The Checker did not approve this item. Check the details before you approve.", "Approve anyway", "Cancel").then(function (y) { if (y) { go(); } });
    } else {
      go();
    }
  }

  function approvalCard(item) {
    var p = item.payload || {};
    var card = el("div", "approval");
    var top = el("div", "approval-top");
    top.appendChild(el("b", "", "#" + item.id + " " + (item.request_type || "")));
    top.appendChild(verdictBadge(p.checker_verdict));
    card.appendChild(top);
    card.appendChild(el("p", "", p.request || "(no request text)"));
    var more = el("div", "");
    more.hidden = true;
    more.appendChild(writerNode(p.writer_result));
    more.appendChild(verdictExtras(p.checker_verdict));
    card.appendChild(more);
    var view = el("button", "btn secondary", "View details");
    view.type = "button";
    view.addEventListener("click", function () {
      more.hidden = !more.hidden;
      view.textContent = more.hidden ? "View details" : "Hide details";
    });
    card.appendChild(view);
    var actions = el("div", "actions");
    var ok = el("button", "btn approve", "Approve");
    ok.type = "button";
    var no = el("button", "btn danger", "Reject");
    no.type = "button";
    ok.addEventListener("click", function () { decide(item, "approve", card); });
    no.addEventListener("click", function () { decide(item, "reject", card); });
    actions.appendChild(ok);
    actions.appendChild(no);
    card.appendChild(actions);
    return card;
  }

  function renderApprovals() {
    var box = $("approvals");
    clear(box);
    var mine = state.pending.filter(function (i) { return i.project === state.project; });
    var other = state.pending.length - mine.length;
    if (!mine.length) { box.appendChild(el("p", "muted small", "No approval requests for this project.")); }
    mine.forEach(function (item) { box.appendChild(approvalCard(item)); });
    if (other > 0) { box.appendChild(el("p", "muted small", other + " more waiting in other projects.")); }
  }

  function agentOutput(name, t) {
    if (!t || !t.result) { return "No output yet for this project."; }
    var r = t.result;
    if (name === "Manager") { return r.route ? "Routed to: " + r.route + (r.reason ? " (" + r.reason + ")" : "") : "No route."; }
    if (name === "Writer") {
      var w = r.writer_result;
      return (w && w.title) ? "Draft: " + w.title + " (" + (w.verified || 0) + " verified, " + (w.not_found || 0) + " NOT FOUND)" : "No draft.";
    }
    if (name === "Checker") { return r.checker_verdict ? "Verdict: " + verdictOf(r.checker_verdict) : "No verdict."; }
    return "";
  }

  function renderAgents() {
    var box = $("agents");
    clear(box);
    box.appendChild(el("p", "muted small", "Agents are shared by every project for now."));
    var mine = projectTasks();
    var active = mine.filter(isActive)[0];
    var withResult = mine.filter(function (t) { return t.result; })[0];
    state.agents.forEach(function (a) {
      var wrap = el("div", "agent" + (a.built ? "" : " off"));
      var head = el("button", "agent-head");
      head.type = "button";
      var cls = "status", label = "Not built yet";
      if (a.built && active) { cls += " s-run"; label = "Working"; }
      else if (a.built) { cls += " s-active"; label = "Active"; }
      head.appendChild(el("span", cls));
      head.appendChild(el("b", "", a.name));
      head.appendChild(el("small", "", label));
      var body = el("div", "agent-body");
      body.hidden = !state.open[a.name];
      body.appendChild(el("p", "", a.job));
      if (a.built) {
        var bar = el("div", "progress" + (active ? " run" : ""));
        bar.appendChild(el("span"));
        body.appendChild(bar);
        body.appendChild(el("p", "", agentOutput(a.name, withResult)));
        body.appendChild(el("p", "kv", "Recent log"));
        if (!mine.length) { body.appendChild(el("p", "", "No tasks yet for this project.")); }
        mine.slice(0, 3).forEach(function (t) {
          body.appendChild(el("p", "", fmtTime(t.created) + " " + t.status + ": " + clip(t.request, 40)));
        });
      }
      head.addEventListener("click", function () {
        state.open[a.name] = !state.open[a.name];
        body.hidden = !state.open[a.name];
        head.setAttribute("aria-expanded", String(!!state.open[a.name]));
      });
      head.setAttribute("aria-expanded", String(!!state.open[a.name]));
      wrap.appendChild(head);
      wrap.appendChild(body);
      box.appendChild(wrap);
    });
  }

  function renderFocus() {
    var ul = $("focus-list");
    clear(ul);
    var run = runningCount();
    ul.appendChild(el("li", "", run ? run + " task(s) running" : "No task running"));
    var p = state.status ? state.status.pending : null;
    ul.appendChild(el("li", "", (p === null || p === undefined) ? "Approval count unavailable" : p + " item(s) waiting for your approval"));
    var link = !state.checked ? "Checking the model link" : (!state.status ? "Server not reachable" : (state.status.model_link ? "Model link is working" : "Model link is down. Start FreeLLMAPI."));
    ul.appendChild(el("li", "", link));
  }

  function renderActivity() {
    var ul = $("activity-list");
    clear(ul);
    if (!state.tasks.length) { ul.appendChild(el("li", "muted", "No activity yet. Run a request to see it here.")); return; }
    state.tasks.slice(0, 6).forEach(function (t) {
      ul.appendChild(el("li", "", t.project + ": " + clip(t.request, 48) + " [" + t.status + "]"));
    });
  }

  function renderIfChanged(key, data, fn) {
    var s = JSON.stringify(data);
    if (state.sig[key] === s) { return; }
    state.sig[key] = s;
    fn();
  }

  function renderAll() {
    updateConn();
    updateOrb();
    renderFocus();
    renderActivity();
    renderIfChanged("agents", { a: state.agents, t: projectTasks(), p: state.project }, renderAgents);
    renderIfChanged("approvals", { p: state.pending, pr: state.project }, renderApprovals);
  }

  function refresh() {
    if (document.hidden) { return Promise.resolve(); }
    return Promise.all([
      api("GET", "/api/status").then(function (s) { state.status = s; state.agents = s.agents || []; }, function () { state.status = null; }),
      api("GET", "/api/tasks").then(function (r) { state.tasks = r.tasks || []; }, function () {}),
      api("GET", "/api/pending").then(function (r) { state.pending = r.items || []; }, function () {})
    ]).then(function () { state.checked = true; renderAll(); });
  }

  var CH_FIELDS = ["tone", "style", "audience", "language", "caption_style", "colors", "rules"];

  function loadChannel(name) {
    if (!$("ch-card")) { return; }
    api("GET", "/api/channel?project=" + encodeURIComponent(name)).then(function (r) {
      if (state.project !== name) { return; }
      var s = r.settings || {};
      CH_FIELDS.forEach(function (k) { $("ch-" + k).value = s[k] || ""; });
      $("ch-topics").value = (s.topics || []).join("\n");
      $("ch-status").textContent = "";
    }).catch(function (e) { $("ch-status").textContent = "Could not load: " + e.message; });
  }

  function saveChannel(ev) {
    ev.preventDefault();
    var data = {};
    CH_FIELDS.forEach(function (k) { data[k] = $("ch-" + k).value; });
    data.topics = $("ch-topics").value.split("\n");
    var btn = $("ch-save");
    btn.disabled = true;
    api("POST", "/api/channel", { project: state.project, settings: data }).then(function () {
      toast("Channel style saved for " + state.project + ".", "ok");
      loadChannel(state.project);
    }).catch(function (e) { toast(e.message, "err"); }).finally(function () { btn.disabled = false; });
  }

  document.addEventListener("submit", function (ev) {
    if (ev.target && ev.target.id === "ch-form") { saveChannel(ev); }
  });

  function selectProject(name) {
    state.project = name;
    document.querySelectorAll(".proj").forEach(function (b) {
      var on = b.getAttribute("data-project") === name;
      b.classList.toggle("active", on);
      if (on) { b.setAttribute("aria-current", "true"); } else { b.removeAttribute("aria-current"); }
    });
    $("project-name").textContent = name;
    $("request").placeholder = "Tell MAY what to do for " + name;
    try { localStorage.setItem("may.project", name); } catch (e) { /* storage may be blocked */ }
    state.sig = {};
    renderAgents();
    renderApprovals();
    if (state.currentTask && state.currentTask.project === name) { renderResult(state.currentTask); }
    else { $("result").hidden = true; }
    loadChannel(name);
    if (!reduced() && $("agents").animate) {
      var kf = [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }];
      $("agents").animate(kf, { duration: 200 });
      $("approvals").animate(kf, { duration: 200 });
    }
  }

  function drawer(which, open) {
    $(which).classList.toggle("open", open);
    $("scrim").hidden = !($("left").classList.contains("open") || $("right").classList.contains("open"));
  }
  function closeDrawers() { drawer("left", false); drawer("right", false); }

  function drawChart() {
    var svg = $("chart"), NS = "http://www.w3.org/2000/svg";
    clear(svg);
    var w = 400, h = 120, pad = 8, max = Math.max.apply(null, DEMO), n = DEMO.length;
    var pts = DEMO.map(function (v, i) {
      return [pad + i * (w - 2 * pad) / (n - 1), h - pad - (v / max) * (h - 2 * pad)];
    });
    [0.25, 0.5, 0.75].forEach(function (f) {
      var g = document.createElementNS(NS, "line");
      g.setAttribute("x1", 0); g.setAttribute("x2", w);
      g.setAttribute("y1", h * f); g.setAttribute("y2", h * f);
      g.setAttribute("class", "grid");
      svg.appendChild(g);
    });
    var d = "M" + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = document.createElementNS(NS, "path");
    area.setAttribute("d", d + " L" + pts[n - 1][0].toFixed(1) + "," + (h - pad) + " L" + pts[0][0].toFixed(1) + "," + (h - pad) + " Z");
    area.setAttribute("class", "area");
    svg.appendChild(area);
    var line = document.createElementNS(NS, "path");
    line.setAttribute("d", d);
    line.setAttribute("class", "line");
    line.setAttribute("pathLength", "1000");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(line);
  }

  function start() {
    if (state.started) { return; }
    state.started = true;
    refresh();
    state.pollTimer = setInterval(refresh, 5000);
  }

  function wire() {
    document.querySelectorAll(".proj").forEach(function (b) {
      b.addEventListener("click", function () {
        selectProject(b.getAttribute("data-project"));
        drawer("left", false);
      });
    });
    $("collapse").addEventListener("click", function () {
      var c = $("app").classList.toggle("collapsed");
      this.setAttribute("aria-label", c ? "Expand sidebar" : "Collapse sidebar");
      this.title = c ? "Expand sidebar" : "Collapse sidebar";
    });
    $("open-left").addEventListener("click", function () { drawer("left", true); });
    $("open-right").addEventListener("click", function () { drawer("right", true); });
    $("close-right").addEventListener("click", function () { drawer("right", false); });
    $("scrim").addEventListener("click", closeDrawers);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeDrawers(); } });
    document.addEventListener("visibilitychange", function () { if (!document.hidden && state.started) { refresh(); } });

    $("command").addEventListener("submit", submit);
    $("cancel-task").addEventListener("click", cancelTask);
    document.querySelectorAll(".quick [data-prefix]").forEach(function (b) {
      b.addEventListener("click", function () {
        var input = $("request"), v = input.value;
        PREFIXES.forEach(function (p) { if (v.indexOf(p) === 0) { v = v.slice(p.length); } });
        input.value = b.getAttribute("data-prefix") + v;
        input.focus();
      });
    });

    $("attach").addEventListener("click", function () { $("file").click(); });
    $("file").addEventListener("change", function () {
      var input = this, f = input.files && input.files[0];
      if (!f) { return; }
      if (f.size > 100000) { toast("File too large. Use a text file under 100 KB.", "err"); input.value = ""; return; }
      var rd = new FileReader();
      rd.onload = function () {
        var t = String(rd.result || "");
        if (t.length > 20000) { toast("The file has more than 20,000 characters. Shorten it.", "err"); return; }
        $("notes").value = t;
        toast("Loaded " + f.name + " as source notes.", "ok");
      };
      rd.onerror = function () { toast("Could not read the file.", "err"); };
      rd.readAsText(f);
      input.value = "";
    });

    $("login-form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var btn = ev.target.querySelector("button[type=submit]");
      btn.disabled = true;
      $("login-error").textContent = "";
      api("POST", "/api/login", { password: $("password").value }).then(function () {
        $("password").value = "";
        $("login").hidden = true;
        start();
      }).catch(function (e) {
        $("login-error").textContent = e.message;
      }).finally(function () { btn.disabled = false; });
    });

    $("open-settings").addEventListener("click", function () { openDialog($("settings")); });
    $("close-settings").addEventListener("click", function () { closeDialog($("settings")); });
    $("logout").addEventListener("click", function () {
      closeDialog($("settings"));
      if (state.noLogin) { toast("Login is switched off (--no-login), so there is nothing to log out of.", "warn"); return; }
      api("POST", "/api/logout", {}).then(function () { resetData(); needLogin(); })
        .catch(function (e) { toast(e.message, "err"); });
    });
  }

  function init() {
    drawChart();
    tickClock();
    setInterval(tickClock, 1000);
    wire();
    var names = [];
    document.querySelectorAll(".proj").forEach(function (b) { names.push(b.getAttribute("data-project")); });
    var saved = null;
    try { saved = localStorage.getItem("may.project"); } catch (e) { saved = null; }
    selectProject(names.indexOf(saved) >= 0 ? saved : DEFAULT_PROJECT);
    updateOrb();
    api("GET", "/api/session").then(function (s) {
      state.noLogin = !!s.no_login;
      if (s.logged_in) { start(); } else { needLogin(); }
    }).catch(function () {
      state.checked = true;
      state.status = null;
      renderAll();
    });
  }

  init();
})();