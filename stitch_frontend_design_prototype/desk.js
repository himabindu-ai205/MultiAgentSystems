/* Wire the FAQ screen to the live ticket API. One ticket at a time. */
(function () {
  const $ = (id) => document.getElementById(id);

  function toast(text, kind) {
    let bar = $("desk-toast");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "desk-toast";
      bar.style.cssText =
        "position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:80;" +
        "background:#0f2744;color:#f3f6fa;padding:10px 16px;border-radius:2px;font:13px IBM Plex Sans,sans-serif;" +
        "max-width:640px";
      document.body.appendChild(bar);
    }
    bar.style.background = kind === "error" ? "#9f1d1d" : "#0f2744";
    bar.textContent = text;
    bar.hidden = false;
    clearTimeout(bar._hide);
    bar._hide = setTimeout(() => {
      bar.hidden = true;
    }, 6000);
  }

  function hideToast() {
    const bar = $("desk-toast");
    if (!bar) return;
    clearTimeout(bar._hide);
    bar.hidden = true;
  }

  function apiUrl(path) {
    const base = String(window.DESK_API || "").replace(/\/$/, "");
    return base + path;
  }

  async function api(path, options) {
    const response = await fetch(apiUrl(path), {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error || response.statusText);
      error.data = data;
      throw error;
    }
    return data;
  }

  function thread() {
    return $("desk-thread");
  }

  function setEmpty(show) {
    const empty = $("desk-empty");
    if (empty) empty.hidden = !show;
  }

  function fillBot(row, view, message) {
    const draft = view && view.draft_text;
    row.querySelector(".desk-draft").textContent =
      draft ||
      (view && view.routing_reason) ||
      message ||
      "No draft. A person needs to look at this ticket.";
    const source = view && view.citation ? view.citation : "";
    const checked = view && view.last_checked ? "Last checked " + view.last_checked : "";
    const meta = row.querySelector(".desk-meta");
    meta.replaceChildren();
    if (draft && source) {
      const link = document.createElement("a");
      link.href = source;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = source;
      meta.appendChild(link);
    }
    row.querySelector(".desk-checked").textContent = draft ? checked : "";
    const verdict = view && view.verdict ? view.verdict : "NOT REVIEWED";
    const reasons = view && view.failure_types && view.failure_types.length
      ? " — " + view.failure_types.join(", ")
      : "";
    const verdictEl = row.querySelector(".desk-verdict");
    const passed = verdict === "PASS" && !(view && view.flagged);
    verdictEl.hidden = passed;
    if (!passed) {
      const review = view && view.flagged
        ? "Reviewer: FLAG — this draft cannot be approved." + reasons
        : "Reviewer: " + verdict + reasons;
      verdictEl.textContent = review;
      verdictEl.dataset.kind = view && view.flagged ? "flag" : "hold";
    }
    const controls = (view && view.controls) || [];
    const left = view && typeof view.refinements_left === "number" ? view.refinements_left : 0;
    row.querySelector(".desk-refine").hidden = !controls.includes("refine");
    row.querySelector(".desk-refine").textContent = "Refine (" + left + " left)";
    row.querySelector(".desk-approve").hidden = !controls.includes("approve");
    row.querySelector(".desk-approve").textContent = "Approve";
    row.querySelector(".desk-retry").hidden = !controls.includes("retry");
    row.querySelector(".desk-discard").hidden = false;
  }

  function showRefineNote(turn, text, kind) {
    const note = turn && turn.querySelector(".desk-note");
    if (!note) return;
    note.hidden = !text;
    note.textContent = text || "";
    if (kind) note.dataset.kind = kind;
    else delete note.dataset.kind;
  }

  function bindDiscard(button, turn) {
    button.addEventListener("click", () => discard(turn));
  }

  function makeBotCard(view, message) {
    const row = document.createElement("div");
    row.className = "desk-bot";
    row.innerHTML =
      '<p class="desk-draft"></p>' +
      '<p class="desk-meta"></p>' +
      '<p class="desk-checked"></p>' +
      '<p class="desk-verdict"></p>' +
      '<div class="desk-actions">' +
      '<button type="button" class="desk-refine">Refine</button>' +
      '<p class="desk-note" hidden></p>' +
      '<button type="button" class="desk-approve">Approve</button>' +
      '<button type="button" class="desk-retry">Retry write</button>' +
      '<button type="button" class="desk-discard">Discard</button>' +
      "</div>";
    fillBot(row, view, message);
    row.querySelector(".desk-refine").addEventListener("click", () => refine());
    row.querySelector(".desk-approve").addEventListener("click", () => approve());
    row.querySelector(".desk-retry").addEventListener("click", () => retryWrite());
    return row;
  }

  function makeUserRow(text) {
    const row = document.createElement("div");
    row.className = "desk-user";
    row.textContent = text;
    return row;
  }

  let busy = false;
  let lastComplaint = "";
  let lastTurn = null;
  let turnSeq = 0;

  function ticketList() {
    return $("ticket-list");
  }

  function verdictLabel(view) {
    if (!view) return "Held";
    if (view.flagged) return "Flag";
    if (view.verdict === "PASS") return "Pass";
    return view.verdict || "Open";
  }

  function tagKind(view) {
    if (view && view.flagged) return "flag";
    if (view && view.verdict === "PASS") return "pass";
    return "hold";
  }

  function syncRail() {
    const turns = thread() ? thread().querySelectorAll(".desk-turn") : [];
    const empty = $("rail-empty");
    const count = $("ticket-count");
    if (empty) empty.hidden = turns.length > 0;
    if (count) count.textContent = turns.length ? String(turns.length) : "";
  }

  function railButton(wrap) {
    return document.querySelector('.ticket[data-for="' + wrap.dataset.id + '"]');
  }

  function paintTag(button, label, kind) {
    const tag = button && button.querySelector(".ticket-tag");
    if (!tag) return;
    tag.textContent = label;
    tag.dataset.kind = kind;
  }

  function addRail(wrap, text, view) {
    const list = ticketList();
    if (!list) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ticket";
    button.dataset.for = wrap.dataset.id;
    button.innerHTML = '<span class="ticket-q"></span><span class="ticket-tag"></span>';
    button.querySelector(".ticket-q").textContent = text;
    paintTag(button, verdictLabel(view), tagKind(view));
    button.addEventListener("click", () => selectTurn(wrap));
    list.appendChild(button);
    syncRail();
  }

  function updateRail(wrap, view) {
    if (!wrap) return;
    paintTag(railButton(wrap), verdictLabel(view), tagKind(view));
  }

  function selectTurn(wrap) {
    const box = thread();
    if (!box || !wrap) return;
    box.querySelectorAll(".desk-turn").forEach((turn) => {
      turn.hidden = turn !== wrap;
    });
    document.querySelectorAll(".ticket").forEach((button) => {
      const on = button.dataset.for === wrap.dataset.id;
      button.classList.toggle("is-current", on);
      if (on) button.setAttribute("aria-current", "true");
      else button.removeAttribute("aria-current");
    });
    const title = $("sheet-title");
    const question = ((wrap.querySelector(".desk-user") || {}).textContent || "").trim();
    if (title) title.textContent = question || "The reply sits here";
    setEmpty(false);
    const button = railButton(wrap);
    if (button && button.scrollIntoView) button.scrollIntoView({ block: "nearest" });
  }

  function freezeOlderTurns() {
    const box = thread();
    if (!box) return;
    box.querySelectorAll(".desk-turn").forEach((turn) => {
      turn.querySelectorAll(".desk-refine, .desk-approve, .desk-retry").forEach((button) => {
        button.hidden = true;
      });
    });
  }

  function appendTurn(userText, view, message) {
    const box = thread();
    if (!box) return;
    setEmpty(false);
    freezeOlderTurns();
    const wrap = document.createElement("div");
    wrap.className = "desk-turn";
    wrap.dataset.id = "t" + (++turnSeq);
    const bot = makeBotCard(view, message);
    wrap.appendChild(makeUserRow(userText));
    wrap.appendChild(bot);
    bindDiscard(bot.querySelector(".desk-discard"), wrap);
    box.appendChild(wrap);
    lastTurn = wrap;
    lastComplaint = userText;
    addRail(wrap, userText, view);
    selectTurn(wrap);
  }

  function replaceDraft(view, message) {
    if (!lastTurn) {
      appendTurn(lastComplaint, view, message);
      return;
    }
    const old = lastTurn.querySelector(".desk-bot");
    const next = makeBotCard(view, message);
    bindDiscard(next.querySelector(".desk-discard"), lastTurn);
    if (old) lastTurn.replaceChild(next, old);
    else lastTurn.appendChild(next);
    updateRail(lastTurn, view);
    selectTurn(lastTurn);
  }

  async function runComplaint(complaint, { asNew } = { asNew: true }) {
    if (busy) return;
    busy = true;
    toast("Looking up the scheme facts…");
    try {
      const view = await api("/api/tickets", {
        method: "POST",
        body: JSON.stringify({ complaint }),
      });
      if (asNew) appendTurn(complaint, view, view.notice || "");
      else replaceDraft(view, view.notice || "");
      toast(view.notice || "Draft ready.");
    } catch (error) {
      const view = error.data && error.data.view;
      if (asNew) appendTurn(complaint, view, error.message);
      else replaceDraft(view, error.message);
      toast(error.message, "error");
    } finally {
      busy = false;
    }
  }

  async function send() {
    const input = $("desk-input");
    const text = (input && input.value.trim()) || "";
    if (!text) {
      toast("Type a factual question first, then press Ask.", "error");
      if (input) input.focus();
      return;
    }
    if (input) input.value = "";
    await runComplaint(text, { asNew: true });
  }

  async function refine() {
    if (busy) return;
    if (!lastTurn) {
      toast("Nothing to refine yet.", "error");
      return;
    }
    const input = $("desk-input");
    const typed = (input && input.value.trim()) || "";
    const feedback = typed || "rerun";
    if (input) input.value = "";
    busy = true;
    toast(typed ? "Updating the draft…" : "Running Facts, Pattern and Compliance again…");
    try {
      const view = await api("/api/tickets/current/refine", {
        method: "POST",
        body: JSON.stringify({ feedback }),
      });
      replaceDraft(view, view.notice || "");
      const written = view.notice || (typed ? "Draft updated." : "Draft re-written.");
      showRefineNote(lastTurn, written);
      hideToast();
    } catch (error) {
      const view = error.data && error.data.view;
      if (view) replaceDraft(view, error.message);
      showRefineNote(lastTurn, error.message, "error");
      hideToast();
    } finally {
      busy = false;
    }
  }

  function showAbove(turn, text) {
    if (!turn) return;
    let note = turn.querySelector(".desk-above");
    if (!text) {
      if (note) note.remove();
      return;
    }
    if (!note) {
      note = document.createElement("p");
      note.className = "desk-above";
      const card = turn.querySelector(".desk-bot");
      if (card) turn.insertBefore(note, card);
      else turn.appendChild(note);
    }
    note.textContent = text;
  }

  function showApproved(turn, message) {
    const actions = turn && turn.querySelector(".desk-actions");
    if (!actions) return;
    actions.innerHTML = '<button type="button" class="desk-discard">Discard</button>';
    bindDiscard(actions.querySelector(".desk-discard"), turn);
    showAbove(turn, message || "Approved. Nothing was sent.");
    const button = railButton(turn);
    paintTag(button, "Approved", "pass");
    hideToast();
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function discard(turn) {
    const target = turn || lastTurn;
    if (!target || busy || target.dataset.removing) return;
    target.dataset.removing = "1";
    const current = target === lastTurn;
    if (current) {
      try {
        await api("/api/tickets/current/discard", { method: "POST", body: "{}" });
      } catch (error) {
        const msg = (error.data && error.data.error) || error.message || "";
        if (!/no open ticket|closed/i.test(msg)) {
          delete target.dataset.removing;
          toast(msg, "error");
          return;
        }
      }
    }
    const note = document.createElement("p");
    note.className = "desk-removed";
    note.textContent = "Removed from this desk.";
    const card = target.querySelector(".desk-bot");
    if (card) target.insertBefore(note, card);
    else target.appendChild(note);
    hideToast();
    await wait(1200);
    const button = railButton(target);
    if (button) button.remove();
    const wasOpen = !target.hidden;
    target.remove();
    rememberLastTurn();
    syncRail();
    if (lastTurn && wasOpen) selectTurn(lastTurn);
    else if (!lastTurn) {
      const title = $("sheet-title");
      if (title) title.textContent = "The reply sits here";
    }
  }

  async function approve() {
    if (busy) return;
    try {
      const data = await api("/api/tickets/current/approve", { method: "POST", body: "{}" });
      showApproved(lastTurn, data.message);
    } catch (error) {
      const view = error.data && error.data.view;
      if (view) replaceDraft(view, error.message);
      toast(error.message || "Approve failed.", "error");
    }
  }

  async function retryWrite() {
    if (busy) return;
    try {
      const data = await api("/api/tickets/current/retry", { method: "POST", body: "{}" });
      showApproved(lastTurn, data.message);
    } catch (error) {
      const view = error.data && error.data.view;
      if (view) replaceDraft(view, error.message);
      toast(error.message || "Retry failed.", "error");
    }
  }

  function rememberLastTurn() {
    const turns = thread() ? thread().querySelectorAll(".desk-turn") : [];
    lastTurn = turns.length ? turns[turns.length - 1] : null;
    lastComplaint = lastTurn
      ? ((lastTurn.querySelector(".desk-user") || {}).textContent || "")
      : "";
    setEmpty(!lastTurn);
  }

  function bind() {
    const input = $("desk-input");
    const sendBtn = $("desk-send");
    if (sendBtn) sendBtn.addEventListener("click", (event) => {
      event.preventDefault();
      send();
    });
    if (input) {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          send();
        }
      });
    }
    const form = $("desk-form");
    if (form) form.addEventListener("submit", (event) => {
      event.preventDefault();
      send();
    });
    document.querySelectorAll("[data-example]").forEach((chip) => {
      chip.addEventListener("click", () => {
        if (input) input.value = chip.getAttribute("data-example") || "";
        send();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
