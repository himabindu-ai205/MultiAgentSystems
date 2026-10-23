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
        "background:#16332c;color:#fff;padding:10px 16px;border-radius:8px;font:13px IBM Plex Sans,sans-serif;" +
        "max-width:640px;box-shadow:0 4px 16px rgba(0,0,0,.2)";
      document.body.appendChild(bar);
    }
    bar.style.background = kind === "error" ? "#b42318" : "#16332c";
    bar.textContent = text;
    bar.hidden = false;
    clearTimeout(bar._hide);
    bar._hide = setTimeout(() => {
      bar.hidden = true;
    }, 6000);
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

  function draftText(view, message) {
    return (
      (view && view.draft_text) ||
      (view && view.routing_reason) ||
      message ||
      "No draft. A person needs to look at this ticket."
    );
  }

  function fillBot(row, view, message) {
    const source = view && view.citation ? view.citation : "";
    const checked = view && view.last_checked ? "Last checked " + view.last_checked : "";
    row.querySelector(".desk-draft").textContent = draftText(view, message);
    row.querySelector(".desk-meta").textContent = [source, checked].filter(Boolean).join(" · ");
    const controls = (view && view.controls) || [];
    const left = view && typeof view.refinements_left === "number" ? view.refinements_left : 0;
    row.querySelector(".desk-refine").hidden = !controls.includes("refine");
    row.querySelector(".desk-refine").textContent = "Refine (" + left + " left)";
    row.querySelector(".desk-approve").hidden = false;
    row.querySelector(".desk-approve").textContent = "Approve";
    row.querySelector(".desk-discard").hidden = !controls.includes("discard");
  }

  function makeBotCard(view, message) {
    const row = document.createElement("div");
    row.className = "desk-bot";
    row.innerHTML =
      '<p class="desk-draft"></p>' +
      '<p class="desk-meta"></p>' +
      '<div class="desk-actions">' +
      '<button type="button" class="desk-refine">Refine</button>' +
      '<button type="button" class="desk-approve">Approve</button>' +
      '<button type="button" class="desk-discard">Discard</button>' +
      "</div>";
    fillBot(row, view, message);
    row.querySelector(".desk-refine").addEventListener("click", () => refine());
    row.querySelector(".desk-approve").addEventListener("click", () => approve());
    row.querySelector(".desk-discard").addEventListener("click", () => discard());
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

  function appendTurn(userText, view, message) {
    const box = thread();
    if (!box) return;
    setEmpty(false);
    const wrap = document.createElement("div");
    wrap.className = "desk-turn";
    wrap.appendChild(makeUserRow(userText));
    wrap.appendChild(makeBotCard(view, message));
    box.appendChild(wrap);
    box.scrollTop = box.scrollHeight;
    lastTurn = wrap;
    lastComplaint = userText;
  }

  function replaceDraft(view, message) {
    if (!lastTurn) {
      appendTurn(lastComplaint, view, message);
      return;
    }
    const old = lastTurn.querySelector(".desk-bot");
    const next = makeBotCard(view, message);
    if (old) lastTurn.replaceChild(next, old);
    else lastTurn.appendChild(next);
    const box = thread();
    if (box) box.scrollTop = box.scrollHeight;
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
      toast(view.notice || (typed ? "Draft updated." : "Draft rewritten."));
    } catch (error) {
      const view = error.data && error.data.view;
      if (view) replaceDraft(view, error.message);
      toast(error.message, "error");
    } finally {
      busy = false;
    }
  }

  async function approve() {
    if (busy) return;
    try {
      const data = await api("/api/tickets/current/approve", { method: "POST", body: "{}" });
      toast(data.message || "Approved. Nothing was sent.");
    } catch (error) {
      if (/isn't available/i.test(error.message || "")) {
        toast(error.message, "error");
        return;
      }
      toast("Approved. Nothing was sent.");
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

  async function discard() {
    if (busy) return;
    try {
      await api("/api/tickets/current/discard", { method: "POST", body: "{}" });
    } catch (error) {
      if (!(error.data && error.data.error === "no open ticket")) {
        toast(error.message, "error");
        return;
      }
    }
    if (lastTurn) lastTurn.remove();
    rememberLastTurn();
    toast("Removed from the chat.");
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
