/* Why-Ledger UI — vanilla JS; presentation only, behaviour matches API SSE contract. */
(function () {
  const $ = (id) => document.getElementById(id);

  const LOAN_CATALOG = {
    "LN-1042": { applicant_id: "AP-1042", amount: 95000, term: 36, sector: "services", outcome: "decline", scorecard: 105 },
    "LN-1107": { applicant_id: "AP-1107", amount: 80000, term: 24, sector: "retail", outcome: "approve", scorecard: null },
    "LN-1230": { applicant_id: "AP-1230", amount: 110000, term: 36, sector: "technology", outcome: "decline", scorecard: 137 },
    "LN-1301": { applicant_id: "AP-1301", amount: 100000, term: 36, sector: "healthcare", outcome: "decline", scorecard: null },
  };

  const pipeline = {
    scout: null,
    evidence: [],
    drafted: [],
    verification: [],
  };

  let activeSourceKind = "stub";
  let healthLoadSeq = 0;
  let recordedLiveReplayActive = false;
  let claimMetaById = {};
  let lastProvenance = {};

  const REJECTED_VERDICT =
    "REJECTED — could not be matched to evidence rows";

  const STEPS = ["intent", "evidence", "draft", "verify", "card"];

  const DRIVER_FEATURE_LABELS = {
    late_over_15_of_last_6: "3+ late payments (>15 days) in last 6",
    utilisation: "Credit utilisation",
    years_in_business: "Years in business",
    amount_to_income: "Amount vs income",
    bureau_score: "Bureau score",
    defaults: "Bureau defaults",
    sector_risk: "Sector risk",
    enquiries_6m: "Bureau enquiries (6m)",
    declared_income: "Declared income",
  };

  function driverFeatureLabel(feature) {
    return DRIVER_FEATURE_LABELS[feature] || feature.replace(/_/g, " ");
  }

  function formatMoney(n) {
    if (n == null) return "—";
    return `$${Number(n).toLocaleString("en-US")}`;
  }

  function isClaimSqlFailure(reason) {
    return (
      typeof reason === "string" &&
      reason.startsWith("claim SQL failed on Zetaris:")
    );
  }

  function rejectedClaimClass(reason) {
    const base = "claim-rejected";
    return isClaimSqlFailure(reason) ? `${base} claim-sql-failure` : base;
  }

  function setModeBadge(kind) {
    activeSourceKind = kind;
    const badge = $("mode-badge");
    const labels = {
      live: "LIVE",
      stub_replay: "STUB REPLAY",
      recorded_live: "RECORDED LIVE",
      stress: "VERIFIER TEST",
      stub: "STUB",
    };
    const text = labels[kind] || labels.stub;
    badge.textContent = text;
    badge.dataset.mode = kind === "live" ? "live" : kind === "stub" ? "stub" : "replay";
  }

  function syncFooterPadding() {
    const footer = document.querySelector("footer.site-footer");
    if (!footer) return;
    const h = footer.offsetHeight;
    document.documentElement.style.setProperty("--site-footer-height", `${h}px`);
    document.body.style.paddingBottom = `${h}px`;
  }

  function formatOriginalLatencySeconds(sec) {
    const s = Number(sec);
    if (!Number.isFinite(s) || s <= 0) return "—";
    if (s >= 60) return String(Math.round(s));
    return (Math.round(s * 10) / 10).toFixed(1);
  }

  function syncBannerHeight() {
    const banner = $("replay-banner");
    const h =
      banner && banner.classList.contains("visible") ? banner.offsetHeight : 0;
    document.documentElement.style.setProperty("--replay-banner-height", `${h}px`);
    document.body.classList.toggle("has-replay-banner", h > 0);
  }

  function formatRecordedUtc(iso) {
    if (!iso) return "—";
    const normalized = String(iso).replace("Z", "+00:00");
    const dt = new Date(normalized);
    if (Number.isNaN(dt.getTime())) return "—";
    const y = dt.getUTCFullYear();
    const mo = String(dt.getUTCMonth() + 1).padStart(2, "0");
    const d = String(dt.getUTCDate()).padStart(2, "0");
    const h = String(dt.getUTCHours()).padStart(2, "0");
    const mi = String(dt.getUTCMinutes()).padStart(2, "0");
    return `${y}-${mo}-${d} ${h}:${mi} UTC`;
  }

  function loanIdFromQuestion(text) {
    const m = String(text || "").match(/\b(LN-\d{4})\b/i);
    return m ? m[0].toUpperCase() : null;
  }

  function syncLoanPickerActive() {
    const loanId =
      selectedSource() === "verifier_stress"
        ? null
        : loanIdFromQuestion($("question").value);
    document.querySelectorAll(".loan-picks .loan-card, .loan-picks button").forEach((b) => {
      const id = b.dataset.loan;
      if (id) b.classList.toggle("active", loanId && id === loanId);
    });
  }

  function selectedSource() {
    const el = document.querySelector('input[name="source"]:checked');
    return el ? el.value : "server";
  }

  function selectedRecordedVariant() {
    const el = document.querySelector('input[name="recorded_variant"]:checked');
    return el ? el.value : "nightshift";
  }

  function updateStepper() {
    const flags = {
      intent: !!pipeline.scout,
      evidence: pipeline.evidence.length > 0,
      draft: pipeline.drafted.length > 0,
      verify: pipeline.verification.length > 0,
      card: !$("card-body").hidden,
    };

    let activeIdx = 0;
    STEPS.forEach((step, i) => {
      if (flags[step]) activeIdx = i;
    });

    document.querySelectorAll("#run-stepper .stepper-item").forEach((el) => {
      const step = el.dataset.step;
      const idx = STEPS.indexOf(step);
      el.classList.remove("is-active", "is-done");
      if (flags.card) {
        if (flags[step]) el.classList.add("is-done");
        return;
      }
      if (idx < activeIdx && flags[step]) el.classList.add("is-done");
      if (idx === activeIdx && flags[step]) el.classList.add("is-active");
    });
  }

  function resetStepper() {
    document.querySelectorAll("#run-stepper .stepper-item").forEach((el) => {
      el.classList.remove("is-active", "is-done");
    });
  }

  function updateRunSettingsHint() {
    const hint = $("run-settings-hint");
    if (!hint) return;
    const src = selectedSource();
    const srcLabel =
      src === "recorded_live"
        ? "Recorded live"
        : src === "verifier_stress"
          ? "Verifier stress"
          : "Server";
    if (src === "verifier_stress") {
      hint.textContent = srcLabel;
      return;
    }
    const loan = loanIdFromQuestion($("question").value);
    hint.textContent = loan
      ? `${srcLabel} · ${loan}`
      : `${srcLabel} — choose loan or enter question`;
  }

  function setLoanPanelsVisible(visible) {
    const ids = [
      "trace-section",
      "card-empty",
      "card-body",
    ];
    ids.forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (id === "card-empty" && visible) {
        el.hidden = $("card-body") && !$("card-body").hidden;
        return;
      }
      if (id === "card-body" && !visible) {
        el.hidden = true;
        return;
      }
      if (id === "trace-section") {
        el.hidden = !visible || selectedSource() === "verifier_stress";
        return;
      }
      el.hidden = !visible;
    });
    document.querySelectorAll(".panel-whycard, .panel-rejected, .panel-letter").forEach((el) => {
      el.hidden = !visible;
    });
    const badge = $("mode-badge");
    if (badge) badge.hidden = !visible && selectedSource() === "verifier_stress";
    if (!visible) {
      $("card-empty").hidden = true;
      $("card-body").hidden = true;
      const summary = $("whycard-summary");
      if (summary) summary.hidden = true;
      $("case-header").hidden = true;
      clearReasonCheckUi();
    }
  }

  function syncSourcePanels() {
    const src = selectedSource();
    $("recorded-live-sub").hidden = src !== "recorded_live";
    const stress = src === "verifier_stress";
    $("stress-panel").hidden = !stress;
    $("trace-section").hidden = stress;
    const actions = document.querySelector(".run-settings-actions");
    if (actions) actions.hidden = stress;
    updateRunSettingsHint();
    if (stress) {
      const panel = $("run-settings");
      if (panel) panel.open = false;
      recordedLiveReplayActive = false;
      setModeBadge("stress");
      setBanner("PLANTED test claims, not model output — verifier stress table", "stub");
      setProvenanceLoading();
      setLoanPanelsVisible(false);
      loadVerifierStress();
    } else {
      setLoanPanelsVisible(true);
      if (src === "recorded_live") {
        setModeBadge("recorded_live");
        setBanner(null);
        if (!recordedLiveReplayActive) setProvenanceLoading();
      }
    }
  }

  async function loadRecordedLiveCatalog() {
    try {
      const res = await fetch("/replay/recorded-live");
      if (!res.ok) return;
      const data = await res.json();
      const short = data.nightshift_radio_label;
      const labelEl = $("recorded-variant-nightshift-label");
      if (short && labelEl) {
        const input = labelEl.querySelector('input[value="nightshift"]');
        labelEl.textContent = "";
        if (input) labelEl.appendChild(input);
        labelEl.append(` ${short}`);
      }
      const variants = data.variants || [];
      const ns = variants.find((v) => v.id === "nightshift");
      if (ns && labelEl && !short) {
        const input = labelEl.querySelector('input[value="nightshift"]');
        const text = ns.label.split("—")[0].trim();
        labelEl.textContent = "";
        if (input) labelEl.appendChild(input);
        labelEl.append(` ${text}`);
      }
    } catch (_) {
      /* ignore */
    }
  }

  function shortenHash(hash) {
    if (!hash || typeof hash !== "string") return "—";
    const first = hash.split(",")[0].trim();
    if (first.length <= 14) return first;
    return `${first.slice(0, 8)}…${first.slice(-4)}`;
  }

  function sqlTableName(sql) {
    if (!sql) return "—";
    const m = sql.match(/\bFROM\s+([`"\w.]+)/i);
    if (!m) return "—";
    const raw = m[1].replace(/`/g, "");
    const parts = raw.split(".");
    return parts[parts.length - 1] || raw;
  }

  function highlightSql(sql) {
    if (!sql) return "";
    const escaped = sql
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    const keywords =
      /\b(SELECT|FROM|WHERE|JOIN|ON|AND|OR|ORDER BY|GROUP BY|LIMIT|AS|IN|COUNT|CAST|INTO|DESC|ASC|SUBSTR|LIKE)\b/gi;
    return escaped
      .replace(keywords, (m) => `<span class="sql-kw">${m}</span>`)
      .replace(/'([^']*)'/g, "<span class=\"sql-str\">'$1'</span>")
      .replace(/\b(\d+(?:\.\d+)?)\b/g, "<span class=\"sql-num\">$1</span>");
  }

  function clearReasonCheckUi() {
    const reasonBanner = $("reason-check-banner");
    if (reasonBanner) {
      reasonBanner.hidden = true;
      reasonBanner.innerHTML = "";
      reasonBanner.classList.remove("reason-ok");
    }
    const reasonChip = $("reason-check-chip");
    if (reasonChip) {
      reasonChip.hidden = true;
      reasonChip.textContent = "";
      reasonChip.classList.remove("is-ok", "is-bad");
    }
    const provRow = $("prov-reason-check-row");
    const provChip = $("prov-reason-check");
    if (provRow) provRow.hidden = true;
    if (provChip) {
      provChip.textContent = "—";
      provChip.classList.remove("is-ok");
    }
  }

  function setProvenanceLoading() {
    lastProvenance = {};
    clearReasonCheckUi();
    setProvenance({
      zetaris: "—",
      scout: "—",
      model: "—",
      recorded_at: "—",
      latency_ms: null,
      llm_calls: null,
    });
    const lat = $("prov-latency");
    const llm = $("prov-llm");
    if (lat) lat.textContent = "—";
    if (llm) llm.textContent = "—";
  }

  function setProvenance(partial) {
    lastProvenance = { ...lastProvenance, ...partial };
    const z = $("prov-zetaris");
    const scout = $("prov-scout");
    const model = $("prov-model");
    const rec = $("prov-recorded");
    const lat = $("prov-latency");
    const llm = $("prov-llm");

    if (partial.zetaris != null) z.textContent = partial.zetaris;
    if (partial.scout != null) scout.textContent = partial.scout;
    if (partial.model != null) model.textContent = partial.model;
    if (partial.recorded_at != null) {
      rec.textContent = formatRecordedUtc(partial.recorded_at);
    }
    if (partial.latency_ms != null) {
      const sec = Number(partial.latency_ms) / 1000;
      lat.textContent = `${formatOriginalLatencySeconds(sec)} s`;
    } else if (partial.latency_ms === null && "latency_ms" in partial) {
      lat.textContent = "—";
    }
    if (partial.llm_calls != null) llm.textContent = String(partial.llm_calls);
    else if (partial.llm_calls === null && "llm_calls" in partial) {
      llm.textContent = "—";
    }

    $("prov-dot-zetaris").classList.toggle(
      "is-ok",
      /jdbc|live|zetaris/i.test(z.textContent) && z.textContent !== "—"
    );
    $("prov-dot-scout").classList.toggle("is-ok", !!pipeline.scout);
    $("prov-dot-model").classList.toggle(
      "is-ok",
      /nemotron|nightshift|nvidia/i.test(model.textContent)
    );
  }

  function updateCaseHeader(loanId, card) {
    const meta = LOAN_CATALOG[loanId] || {};
    $("case-header").hidden = !loanId && !card;
    if (!loanId && card) {
      const m = String($("question").value || "").match(/\bLN-\d{4}\b/i);
      loanId = m ? m[0].toUpperCase() : null;
    }
    if (loanId) $("case-loan-id").textContent = loanId;
    $("case-amount").textContent = formatMoney(meta.amount);
    $("case-term").textContent = meta.term ? `${meta.term} mo` : "—";
    $("case-sector").textContent = meta.sector || "—";
    let score = meta.scorecard;
    if (card && card.claims) {
      for (const c of card.claims) {
        const m = c.text.match(/scorecard points is ([\d.]+)/i);
        if (m) score = m[1];
      }
    }
    $("case-scorecard").textContent = score != null ? `${score} pts` : "—";
  }

  async function loadVerifierStress() {
    const wrap = $("stress-table-wrap");
    const meta = $("stress-meta");
    const tiles = $("stress-tiles");
    wrap.innerHTML = "<p class='step-muted'>Loading…</p>";
    tiles.hidden = true;
    try {
      const res = await fetch("/verifier/stress");
      const data = await res.json();
      meta.textContent = data.recorded_at
        ? `Recorded ${data.recorded_at} · backend ${data.backend || "—"} · ${data.label || ""}`
        : (data.message || "No stress run on disk yet.");
      const rows = data.rows || [];
      if (!rows.length) {
        wrap.innerHTML =
          "<p class='step-muted'>Run scripts/stress_verifier.py to populate eval/verifier-stress.json.</p>";
        return;
      }
      const t = data.totals || {};
      if (t.cases) {
        tiles.hidden = false;
        const reasonMatch = t.reason_matched != null ? t.reason_matched : t.expect_matched;
        tiles.innerHTML = `
          <div class="stress-tile"><div class="stress-num">${t.cases}</div><div class="stress-label">Cases</div></div>
          <div class="stress-tile"><div class="stress-num">${t.verified ?? "—"}</div><div class="stress-label">Verified</div></div>
          <div class="stress-tile"><div class="stress-num">${t.unsupported ?? "—"}</div><div class="stress-label">Rejected</div></div>
          <div class="stress-tile"><div class="stress-num">${t.expect_matched ?? "—"}/${t.cases}</div><div class="stress-label">Verdict matched</div></div>
          <div class="stress-tile"><div class="stress-num">${reasonMatch ?? "—"}/${t.cases}</div><div class="stress-label">Reason matched</div></div>`;
      }
      const tbl = document.createElement("table");
      tbl.innerHTML =
        "<thead><tr><th>Loan</th><th>Claim</th><th>Planted truth</th><th>Verdict</th><th>Reason</th></tr></thead>";
      const tbody = document.createElement("tbody");
      for (const row of rows) {
        const tr = document.createElement("tr");
        const vClass =
          row.verdict === "VERIFIED" ? "verdict-verified" : "verdict-unsupported";
        tr.innerHTML = `<td>${row.loan_id}</td><td>${row.claim}</td><td>${row.planted_truth}</td><td class="${vClass}">${row.verdict}</td><td>${row.reason}</td>`;
        tbody.appendChild(tr);
      }
      tbl.appendChild(tbody);
      const scroll = document.createElement("div");
      scroll.className = "table-hscroll";
      scroll.appendChild(tbl);
      wrap.innerHTML = "";
      wrap.appendChild(scroll);
      if (t.cases) {
        const p = document.createElement("p");
        p.className = "step-muted";
        const rm = t.reason_matched != null ? t.reason_matched : t.expect_matched;
        p.textContent = `Totals: ${t.cases} cases · VERIFIED ${t.verified} · UNSUPPORTED ${t.unsupported} · verdict matched ${t.expect_matched}/${t.cases} · reason matched ${rm}/${t.cases}`;
        wrap.appendChild(p);
      }
      const backend = data.backend || "—";
      const zetLabel = /jdbc/i.test(backend) ? "live JDBC" : backend;
      setModeBadge("stress");
      setProvenance({
        zetaris: zetLabel,
        scout: "—",
        model: "verifier harness (no LLM)",
        recorded_at: data.recorded_at || "—",
        latency_ms: null,
        llm_calls: null,
      });
    } catch (err) {
      wrap.innerHTML = `<p class='step-muted'>Could not load stress data. ${String(err)}</p>`;
    }
  }

  function setBanner(text, kind) {
    const banner = $("replay-banner");
    if (!text) {
      banner.classList.remove("visible", "stub");
      banner.textContent = "";
      syncBannerHeight();
      return;
    }
    banner.textContent = text;
    banner.classList.add("visible");
    banner.classList.toggle("stub", kind === "stub");
    syncBannerHeight();
  }

  function replayInfoBadgeKind(data) {
    const mode = String(data?.mode || "").toLowerCase();
    if (mode === "recorded_live") return "recorded_live";
    if (mode === "stub_replay") return "stub_replay";
    if (data?.source === "recorded_live") return "recorded_live";
    if (data?.source === "stub") return "stub_replay";
    return null;
  }

  function applyReplayInfo(data) {
    if (!data) return;
    const badgeKind = replayInfoBadgeKind(data);
    if (badgeKind === "recorded_live") recordedLiveReplayActive = true;
    if (data.banner) {
      const bannerKind = badgeKind === "recorded_live" ? "live" : "stub";
      setBanner(data.banner, bannerKind);
    }
    if (badgeKind) setModeBadge(badgeKind);

    const backends = data.backends || {};
    const llmModel = backends.llm_model || "";
    let modelLine = backends.model || "via Meterless Nightshift";
    if (llmModel && /nightshift/i.test(modelLine)) {
      modelLine = `${llmModel} via Meterless Nightshift`;
    } else if (llmModel) {
      modelLine = llmModel;
    }
    setProvenance({
      zetaris: backends.zetaris === "ZETARIS-JDBC" ? "live JDBC" : (backends.zetaris || "—"),
      scout: "Intent engine (ran)",
      model: modelLine,
      recorded_at: data.recorded_at || "—",
      latency_ms: data.latency_ms,
      llm_calls: data.llm_calls,
    });
    syncBannerHeight();
  }

  async function loadHealth() {
    const seq = ++healthLoadSeq;
    if (selectedSource() === "recorded_live") {
      setModeBadge("recorded_live");
      if (!recordedLiveReplayActive) setProvenanceLoading();
      return;
    }
    if (selectedSource() === "verifier_stress") return;
    if (recordedLiveReplayActive) return;
    if (activeSourceKind === "recorded_live" || activeSourceKind === "stub_replay") return;
    try {
      const res = await fetch("/health");
      if (seq !== healthLoadSeq) return;
      if (selectedSource() === "recorded_live" || recordedLiveReplayActive) return;
      const data = await res.json();
      const m = (data.mode || "stub").toLowerCase();
      if (m === "replay") {
        setModeBadge("stub_replay");
        setBanner(
          "Replay (STUB bundles — recorded LLM fixtures, not live Zetaris/Nemotron)",
          "stub"
        );
        setProvenance({ zetaris: "STUB", model: "recorded fixtures" });
      } else if (m === "live") {
        setModeBadge("live");
        setBanner(null);
        setProvenance({ zetaris: "live JDBC", model: "live Nemotron" });
      } else {
        setModeBadge("stub");
        setBanner(null);
        setProvenance({ zetaris: "STUB", model: "STUB LLM" });
      }
      $("api-offline").hidden = true;
    } catch (_) {
      if (seq !== healthLoadSeq) return;
      if (selectedSource() === "recorded_live" || recordedLiveReplayActive) return;
      setModeBadge("stub");
      showApiOffline();
    }
  }

  function showApiOffline() {
    if (location.protocol === "file:") {
      $("api-offline").hidden = false;
    } else {
      fetch("/health")
        .then((r) => {
          if (!r.ok) $("api-offline").hidden = false;
        })
        .catch(() => {
          $("api-offline").hidden = false;
        });
    }
  }

  function resetPipeline() {
    pipeline.scout = null;
    pipeline.evidence = [];
    pipeline.drafted = [];
    pipeline.verification = [];
    claimMetaById = {};
    $("trace-pipeline").innerHTML = "";
    resetStepper();
    $("case-header").hidden = true;
    const summary = $("whycard-summary");
    if (summary) summary.hidden = true;
    clearReasonCheckUi();
  }

  function formatEvidenceRowSummary(ev) {
    const table = sqlTableName(ev.sql);
    const count = ev.row_count;
    if (count === 0 && table === "policy_docs") {
      return { text: "0 rows (no matching policy text)", muted: true };
    }
    if (count === 0) {
      return { text: "0 rows", muted: true };
    }
    if (count != null) {
      return { text: `${count} row(s)`, muted: false };
    }
    return { text: "—", muted: true };
  }

  function renderDriverBars(container, drivers, limit) {
    if (!container) return;
    container.innerHTML = "";
    const aggregated = aggregateDriversForDisplay(drivers);
    const slice = limit ? aggregated.slice(0, limit) : aggregated;
    const maxW = slice[0]?.weight_pct || 100;
    slice.forEach((d) => {
      const li = document.createElement("li");
      li.className = "driver-bar-row";
      const width = maxW > 0 ? (d.weight_pct / maxW) * 100 : 0;
      const label = driverFeatureLabel(d.feature);
      const labelHtml =
        label === d.feature
          ? `#${d.rank} ${label}`
          : `#${d.rank} ${label} <span class="driver-feature-key" title="${d.feature}">${d.feature}</span>`;
      li.innerHTML = `
        <span class="driver-label">${labelHtml}</span>
        <div class="driver-bar-track" title="${d.weight_pct}% share">
          <div class="driver-bar-fill" style="width:${width}%"></div>
        </div>
        <span class="driver-pct">${d.weight_pct}%</span>`;
      container.appendChild(li);
    });
  }

  function renderClaimsSummaryChips(tile, drafted, verified, rejected) {
    if (!tile) return;
    tile.innerHTML = "";
    tile.classList.add("claims-summary-chips");
    const specs = [
      { n: drafted, label: "drafted", cls: "claim-stat-neutral" },
      { n: verified, label: "verified", cls: "claim-stat-verified" },
      { n: rejected, label: "rejected", cls: "claim-stat-rejected" },
    ];
    for (const { n, label, cls } of specs) {
      const chip = document.createElement("span");
      chip.className = `claim-stat-chip ${cls}`;
      chip.textContent = `${n} ${label}`;
      tile.appendChild(chip);
    }
  }

  function collapseRunSettingsAfterRun() {
    const panel = $("run-settings");
    if (panel) panel.open = false;
    updateRunSettingsHint();
  }

  function renderVerifierTable(container) {
    const table = document.createElement("table");
    table.className = "verifier-claims-table";
    table.innerHTML =
      "<thead><tr><th>Claim</th><th>Status</th><th>Evidence IDs</th><th>Proof hash</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const v of pipeline.verification) {
      const draft = pipeline.drafted.find((d) => d.id === v.claim_id);
      const meta = claimMetaById[v.claim_id] || {};
      const shipped = v.status === "VERIFIED" || v.shipped;
      const tr = document.createElement("tr");
      const ids = (draft?.query_ids || meta.query_ids || []).join(", ") || "—";
      tr.innerHTML = `
        <td>${v.claim_text || v.claim_id}</td>
        <td><span class="chip ${shipped ? "chip-verified" : "chip-rejected"}">${shipped ? "Verified" : "Rejected"}</span>${!shipped && v.reason ? `<div class="reason">${v.reason}</div>` : ""}</td>
        <td class="ev-id">${ids}</td>
        <td class="hash">${shortenHash(meta.row_hash)}</td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    const wrap = document.createElement("div");
    wrap.className = "table-hscroll";
    wrap.appendChild(table);
    container.appendChild(wrap);
  }

  function renderPipeline() {
    const root = $("trace-pipeline");
    root.innerHTML = "";

    const scoutBlock = document.createElement("div");
    scoutBlock.className = "step-block";
    scoutBlock.innerHTML = '<p class="step-title">1. Scout Intent check</p>';
    const scoutBody = document.createElement("div");
    scoutBody.className = "step-body";
    if (pipeline.scout) {
      scoutBody.textContent = pipeline.scout.note || JSON.stringify(pipeline.scout);
    } else {
      scoutBody.className = "step-muted";
      scoutBody.textContent = "Waiting…";
    }
    scoutBlock.appendChild(scoutBody);
    root.appendChild(scoutBlock);

    const evBlock = document.createElement("div");
    evBlock.className = "step-block";
    evBlock.innerHTML = '<p class="step-title">2. Evidence queries (Zetaris SQL)</p>';
    if (!pipeline.evidence.length) {
      const p = document.createElement("p");
      p.className = "step-muted";
      p.textContent = "No evidence steps yet.";
      evBlock.appendChild(p);
    } else {
      pipeline.evidence.forEach((ev, i) => {
        const det = document.createElement("details");
        det.className = "evidence-item";
        det.open = i < 2;
        const sum = document.createElement("summary");
        const id = ev.evidence_id || ev.query_id || "(pending id)";
        const table = sqlTableName(ev.sql);
        const rowSummary = formatEvidenceRowSummary(ev);
        const rowsCls = rowSummary.muted ? "ev-rows ev-rows-muted" : "ev-rows";
        sum.innerHTML = `<span class="ev-id">${id}</span><span class="ev-table">${table}</span><span class="${rowsCls}">${rowSummary.text}</span><span class="step-muted">${ev.note || ev.tool || ""}</span>`;
        det.appendChild(sum);
        const pre = document.createElement("pre");
        pre.className = "sql-block";
        pre.innerHTML = highlightSql(ev.sql || "(no sql)");
        det.appendChild(pre);
        evBlock.appendChild(det);
      });
    }
    root.appendChild(evBlock);

    const draftBlock = document.createElement("div");
    draftBlock.className = "step-block";
    draftBlock.innerHTML = '<p class="step-title">3. Nemotron drafted claims</p>';
    if (!pipeline.drafted.length) {
      const p = document.createElement("p");
      p.className = "step-muted";
      p.textContent = "Draft step not reached yet.";
      draftBlock.appendChild(p);
    } else {
      pipeline.drafted.forEach((c) => {
        const p = document.createElement("p");
        p.style.margin = "0.35rem 0";
        p.textContent = `${c.id}: ${c.text}`;
        draftBlock.appendChild(p);
      });
    }
    root.appendChild(draftBlock);

    const verBlock = document.createElement("div");
    verBlock.className = "step-block";
    verBlock.innerHTML = '<p class="step-title">4. Verifier (per claim)</p>';
    if (!pipeline.verification.length) {
      const p = document.createElement("p");
      p.className = "step-muted";
      p.textContent = "Verifier has not run yet.";
      verBlock.appendChild(p);
    } else {
      pipeline.verification.forEach((v) => {
        const div = document.createElement("div");
        const shipped = v.status === "VERIFIED" || v.shipped;
        div.className = shipped ? "claim-verified" : rejectedClaimClass(v.reason);
        const verdict = document.createElement("div");
        verdict.className = "verdict " + (shipped ? "ok" : "bad");
        verdict.textContent = shipped ? "Shipped on card" : REJECTED_VERDICT;
        const text = document.createElement("div");
        text.textContent = v.claim_text || v.claim_id;
        div.appendChild(verdict);
        div.appendChild(text);
        if (!shipped && v.reason) {
          const r = document.createElement("div");
          r.className = "reason";
          r.textContent = v.reason;
          div.appendChild(r);
        }
        verBlock.appendChild(div);
      });
      renderVerifierTable(verBlock);
    }
    root.appendChild(verBlock);

    $("prov-dot-scout").classList.toggle("is-ok", !!pipeline.scout);
    updateStepper();
  }

  function parseSSE(text) {
    const events = [];
    const blocks = text.split("\n\n");
    for (const block of blocks) {
      if (!block.trim()) continue;
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (data) {
        try {
          events.push({ event, data: JSON.parse(data) });
        } catch (_) {
          events.push({ event, data: { raw: data } });
        }
      }
    }
    return events;
  }

  function aggregateDriversForDisplay(drivers) {
    const weights = new Map();
    for (const d of drivers || []) {
      const feat = d.feature;
      weights.set(feat, (weights.get(feat) || 0) + Number(d.weight_pct));
    }
    const sorted = [...weights.entries()].sort((a, b) => b[1] - a[1]);
    return sorted.map(([feature, weight_pct], i) => ({
      rank: i + 1,
      feature,
      weight_pct: Math.round(weight_pct * 10) / 10,
    }));
  }

  function reasonCheckDetailHtml(rc) {
    const code = rc.recorded_reason_code || "";
    const detail = rc.detail || "";
    if (!code || !detail.includes(`The record says ${code}`)) {
      return detail;
    }
    return detail.replace(
      `The record says ${code}`,
      `The record says <code>${code}</code>`
    );
  }

  function reasonEvidenceChipLabel(ev, index) {
    const table = sqlTableName(ev.sql);
    if (table) return table;
    const short = (ev.query_id || "").slice(0, 8);
    return short ? `Query ${short}` : `Evidence ${index + 1}`;
  }

  function openReasonCheckEvidence(ev, rc) {
    $("ev-claim-text").textContent = rc.headline || "Recorded reason check";
    $("ev-verification").textContent = `${rc.status || "?"} — ${rc.detail || ""}`;
    const sqlEl = $("ev-sql");
    if (sqlEl) {
      sqlEl.className = "sql-block";
      sqlEl.innerHTML = highlightSql(ev.sql || "(no sql)");
    }
    $("ev-rows").textContent = JSON.stringify(ev.rows || [], null, 2);
    $("ev-hash").textContent = ev.sha256 ? `sha256: ${ev.sha256}` : "";
    $("evidence-drawer").classList.add("open");
  }

  function renderReasonCheck(card) {
    const banner = $("reason-check-banner");
    const headerChip = $("reason-check-chip");
    const provRow = $("prov-reason-check-row");
    const provChip = $("prov-reason-check");
    const rc = card && card.reason_check;
    if (headerChip) {
      headerChip.hidden = true;
      headerChip.textContent = "";
      headerChip.classList.remove("is-ok", "is-bad");
    }
    if (banner) {
      banner.hidden = true;
      banner.innerHTML = "";
      banner.classList.remove("reason-ok");
    }
    if (!rc || !rc.status || rc.status === "NOT_CHECKED") {
      if (provRow) provRow.hidden = true;
      return;
    }
    if (rc.status === "CONSISTENT" && headerChip) {
      headerChip.hidden = false;
      headerChip.classList.add("is-ok");
      headerChip.textContent = "Reason consistent";
    } else if (rc.status === "UNSUPPORTED" && headerChip) {
      headerChip.hidden = false;
      headerChip.classList.add("is-bad");
      headerChip.textContent = "Reason not supported";
    }
    if (rc.status === "UNSUPPORTED" && banner) {
      banner.hidden = false;
      const headline = rc.headline || "Recorded reason not supported by data";
      const detailHtml = reasonCheckDetailHtml(rc);
      const chips = (rc.evidence || [])
        .slice(0, 2)
        .map((ev, i) => {
          const label = reasonEvidenceChipLabel(ev, i);
          return `<button type="button" class="reason-evidence-chip" data-ev-index="${i}">${label}</button>`;
        })
        .join("");
      banner.innerHTML = `
        <div class="reason-check-hero-head">${headline}</div>
        <p class="reason-check-hero-detail">${detailHtml}</p>
        <div class="reason-check-evidence-row">${chips}</div>`;
      banner.querySelectorAll(".reason-evidence-chip").forEach((btn) => {
        const idx = Number(btn.getAttribute("data-ev-index"));
        const ev = (rc.evidence || [])[idx];
        if (ev) btn.addEventListener("click", () => openReasonCheckEvidence(ev, rc));
      });
    }
    if (provRow && provChip) {
      provRow.hidden = false;
      provChip.textContent =
        rc.status === "UNSUPPORTED" ? "Not supported by data" : "Consistent with data";
      provChip.classList.toggle("is-ok", rc.status === "CONSISTENT");
    }
  }

  function renderCard(card) {
    $("card-empty").hidden = true;
    $("card-body").hidden = false;

    const loanId = loanIdFromQuestion($("question").value);
    updateCaseHeader(loanId, card);

    renderReasonCheck(card);

    const pill = $("decision-pill");
    pill.textContent = card.decision || "unknown";
    pill.className =
      "decision-pill decision-pill-lg " +
      (String(card.decision).toLowerCase() === "approve"
        ? "decision-approve"
        : "decision-decline");

    claimMetaById = {};
    (card.claims || []).forEach((c) => {
      claimMetaById[c.id] = { query_ids: c.query_ids, row_hash: c.row_hash };
    });

    renderDriverBars($("drivers"), card.drivers, null);
    renderDriverBars($("summary-drivers"), card.drivers, 3);
    const summary = $("whycard-summary");
    if (summary) summary.hidden = false;

    const drafted =
      pipeline.drafted.length ||
      (card.claims || []).length + (card.rejected || []).length;
    const verified = (card.claims || []).length;
    const rejected = (card.rejected || []).length;
    renderClaimsSummaryChips($("claims-summary-tile"), drafted, verified, rejected);

    const sumCf = $("summary-counterfactual");
    if (sumCf) {
      if (card.counterfactual) {
        const flip = card.counterfactual.flips_to;
        sumCf.textContent =
          flip === "approve"
            ? `${card.counterfactual.change} → flips to ${flip}`
            : card.counterfactual.change;
        sumCf.classList.add("counterfactual-callout");
      } else {
        sumCf.textContent = "—";
        sumCf.classList.remove("counterfactual-callout");
      }
    }

    collapseRunSettingsAfterRun();

    const claims = $("claims");
    claims.innerHTML = "";
    (card.claims || []).forEach((c) => {
      const row = document.createElement("div");
      row.className = "claim-row";
      const span = document.createElement("span");
      const ids = (c.query_ids || []).map((id) => `[${id}]`).join(" ");
      span.innerHTML = `${c.text} <span class="step-muted">${ids}</span>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Evidence";
      btn.addEventListener("click", () => openEvidence(c.id));
      row.appendChild(span);
      row.appendChild(btn);
      claims.appendChild(row);
    });

    const rej = $("rejected-list");
    rej.innerHTML = "";
    (card.rejected || []).forEach((r) => {
      const div = document.createElement("div");
      const sqlFail = isClaimSqlFailure(r.reason);
      div.className = rejectedClaimClass(r.reason);
      const verdictLabel = sqlFail
        ? "REJECTED — claim SQL failed on Zetaris"
        : REJECTED_VERDICT;
      div.innerHTML = `<div class="verdict bad">${verdictLabel}</div><div>${r.text}</div><div class="reason">${r.reason}</div>`;
      rej.appendChild(div);
    });
    if (!card.rejected || !card.rejected.length) {
      const p = document.createElement("p");
      p.className = "step-muted";
      p.textContent = "No rejections on this run (verifier shipped every draft).";
      rej.appendChild(p);
    }

    const cf = $("counterfactual");
    if (card.counterfactual) {
      const flip = card.counterfactual.flips_to;
      cf.textContent =
        flip === "approve"
          ? `${card.counterfactual.change} → flips to ${flip}`
          : card.counterfactual.change;
      cf.classList.add("counterfactual-callout");
    } else {
      cf.textContent = "—";
      cf.classList.remove("counterfactual-callout");
    }

    const letterBody = $("letter-body");
    if (letterBody) letterBody.textContent = card.applicant_letter || "—";
    else $("letter-box").textContent = card.applicant_letter || "—";

    window.__lastCardId = card.card_id;
    renderPipeline();
    updateStepper();
  }

  async function openEvidence(claimId) {
    const res = await fetch(`/evidence/${encodeURIComponent(claimId)}`);
    if (!res.ok) {
      $("status").textContent = "Could not load evidence.";
      return;
    }
    const data = await res.json();
    $("ev-claim-text").textContent = data.claim_text || claimId;
    const v = data.verification || {};
    $("ev-verification").textContent = `${v.status || "?"} — ${v.reason || ""}`;
    const q = (data.queries && data.queries[0]) || {};
    const sqlEl = $("ev-sql");
    if (sqlEl) {
      sqlEl.className = "sql-block";
      sqlEl.innerHTML = highlightSql(q.sql || "(no sql)");
    }
    $("ev-rows").textContent = JSON.stringify(q.rows || [], null, 2);
    $("ev-hash").textContent = q.sha256 ? `sha256: ${q.sha256}` : "";
    $("evidence-drawer").classList.add("open");
  }

  function applyEvent(ev) {
    if (ev.event === "replay_info") {
      applyReplayInfo(ev.data);
    } else if (ev.event === "scout_intent") {
      pipeline.scout = ev.data;
      renderPipeline();
    } else if (ev.event === "evidence_query") {
      pipeline.evidence.push(ev.data);
      renderPipeline();
    } else if (ev.event === "claims_drafted") {
      pipeline.drafted = ev.data.claims || [];
      renderPipeline();
    } else if (ev.event === "verification") {
      pipeline.verification.push(ev.data);
      renderPipeline();
    } else if (ev.event === "trace" && ev.data.tool === "scout_intent" && !pipeline.scout) {
      pipeline.scout = ev.data;
      renderPipeline();
    } else if (ev.event === "trace" && ev.data.sql) {
      const dup = pipeline.evidence.some((e) => e.sql === ev.data.sql);
      if (!dup) {
        pipeline.evidence.push({
          ...ev.data,
          evidence_id: ev.data.evidence_id || null,
        });
      }
      renderPipeline();
    } else if (ev.event === "card") {
      if (ev.data.replay_source === "recorded_live") {
        recordedLiveReplayActive = true;
        if (ev.data.replay_banner) setBanner(ev.data.replay_banner, "live");
        setModeBadge("recorded_live");
      } else if (ev.data.replay_banner) {
        setBanner(ev.data.replay_banner, "stub");
      }
      renderCard(ev.data);
      if (ev.data.replay_source === "recorded_live") {
        setModeBadge("recorded_live");
      } else if ((ev.data.mode || "").toLowerCase() === "replay") {
        setModeBadge("stub_replay");
      } else if ((ev.data.mode || "").toLowerCase() === "live") {
        setModeBadge("live");
      }
    }
  }

  function initTheme() {
    const stored = localStorage.getItem("whyledger-theme");
    if (stored === "light") document.documentElement.dataset.theme = "light";
    const btn = $("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("whyledger-theme", next);
      btn.setAttribute(
        "aria-label",
        next === "light" ? "Toggle dark theme" : "Toggle light theme"
      );
    });
    const theme = document.documentElement.dataset.theme;
    btn.setAttribute(
      "aria-label",
      theme === "light" ? "Toggle dark theme" : "Toggle light theme"
    );
  }

  $("close-evidence").addEventListener("click", () => {
    $("evidence-drawer").classList.remove("open");
  });

  document.querySelectorAll(".loan-picks [data-loan]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const loan = btn.dataset.loan;
      $("question").value = `Explain the decision for loan ${loan}`;
      syncLoanPickerActive();
      if (selectedSource() !== "verifier_stress") {
        $("ask-btn").click();
      }
    });
  });

  $("question").addEventListener("input", () => {
    syncLoanPickerActive();
    updateRunSettingsHint();
  });

  document.querySelectorAll('input[name="source"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      syncSourcePanels();
      if (selectedSource() === "recorded_live") {
        setModeBadge("recorded_live");
        if (!recordedLiveReplayActive) setProvenanceLoading();
      } else if (selectedSource() !== "verifier_stress") {
        recordedLiveReplayActive = false;
        activeSourceKind = "stub";
        loadHealth();
      }
    });
  });

  $("ask-btn").addEventListener("click", async () => {
    const question = $("question").value.trim();
    resetPipeline();
    renderPipeline();
    $("status").textContent = "Running…";
    $("status").classList.remove("refusal");
    $("ask-btn").disabled = true;
    $("card-empty").hidden = false;
    $("card-body").hidden = true;
    try {
      syncLoanPickerActive();
      const loanId = loanIdFromQuestion(question);
      if (selectedSource() === "verifier_stress") {
        $("status").textContent = "Use the stress table above (planted claims).";
        return;
      }
      const useRecorded = selectedSource() === "recorded_live";
      let res;
      if (useRecorded) {
        setModeBadge("recorded_live");
        if (!loanId) {
          $("status").textContent = "Recorded live replay needs a loan ID (LN-xxxx).";
          return;
        }
        res = await fetch("/replay/recorded-live", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            loan_id: loanId,
            variant: selectedRecordedVariant(),
          }),
        });
      } else {
        const body = question.match(/^LN-\d{4}$/i)
          ? { loan_id: question.toUpperCase() }
          : { question };
        res = await fetch("/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      const text = await res.text();
      const events = parseSSE(text);
      let gotCard = false;
      for (const ev of events) {
        if (ev.event === "refusal") {
          $("status").textContent = ev.data.message || "Refused";
          $("status").classList.add("refusal");
        } else if (ev.event === "error") {
          $("status").textContent = ev.data.message || "Error";
        } else {
          applyEvent(ev);
        }
        if (ev.event === "card") gotCard = true;
      }
      if (gotCard) $("status").textContent = "Done.";
      else if (
        !$("status").classList.contains("refusal") &&
        !$("status").textContent.startsWith("Recorded")
      ) {
        if ($("status").textContent === "Running…") {
          $("status").textContent = "Finished without a card.";
        }
      }
    } catch (err) {
      $("status").textContent = String(err);
      showApiOffline();
    } finally {
      $("ask-btn").disabled = false;
    }
  });

  initTheme();
  syncSourcePanels();
  loadRecordedLiveCatalog();
  loadHealth();
  renderPipeline();
  syncFooterPadding();
  syncBannerHeight();
  window.addEventListener("resize", () => {
    syncFooterPadding();
    syncBannerHeight();
  });
  if (location.protocol === "file:") showApiOffline();
})();
