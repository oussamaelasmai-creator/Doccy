let state = null;

const $ = (selector) => document.querySelector(selector);
const fmt = new Intl.DateTimeFormat("fr-FR");

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Erreur applicative");
  }
  return payload;
}

async function loadState() {
  state = await api("/api/state");
  renderAll();
}

function renderAll() {
  $("#current-user").textContent = state.current_user.name;
  renderAiStatus();
  renderStats();
  renderSelects();
  renderDocuments();
  renderTasks();
  renderAlerts();
}

function renderAiStatus() {
  const embeddings = state.ai_status?.embeddings;
  const ocr = state.ai_status?.ocr;
  const semantic = embeddings?.enabled ? `BGE-M3 actif (${embeddings.provider})` : "BGE-M3 non installe";
  const ocrLabel =
    ocr?.selected === "rapidocr"
      ? "RapidOCR actif"
      : ocr?.selected === "paddleocr"
        ? "PaddleOCR actif"
        : ocr?.selected === "tesseract"
          ? "Tesseract actif"
          : "OCR manquant";
  $("#local-ai-status").textContent = `IA locale: ${ocrLabel}; ${semantic}`;
}

function renderStats() {
  const labels = {
    clients: "Clients",
    matters: "Affaires",
    documents: "Documents",
    indexed: "Indexes",
    semantic_pages: "Vectorises",
    pending: "En attente",
    errors: "A verifier",
  };
  $("#stats").innerHTML = Object.entries(labels)
    .map(([key, label]) => `<div class="stat"><span>${label}</span><strong>${state.stats[key] || 0}</strong></div>`)
    .join("");
}

function renderSelects() {
  fillSelect("#matter-client", state.clients, "Choisir un client");
  fillSelect("#upload-client", state.clients, "Choisir un client");
  fillSelect("#search-client", state.clients, "Tous les clients", true);
  fillSelect("#upload-matter", state.matters, "Choisir une affaire");
  fillSelect("#search-matter", state.matters, "Toutes les affaires", true);
  fillSelect("#task-matter", state.matters, "Choisir une affaire");
}

function fillSelect(selector, rows, placeholder, allowEmpty = false) {
  const element = $(selector);
  const current = element.value;
  element.innerHTML = `${allowEmpty ? `<option value="">${placeholder}</option>` : ""}${rows
    .map((row) => `<option value="${escapeAttr(row.id)}">${escapeHtml(row.name)}</option>`)
    .join("")}`;
  if (current && rows.some((row) => row.id === current)) {
    element.value = current;
  }
}

function renderDocuments() {
  const matterById = Object.fromEntries(state.matters.map((matter) => [matter.id, matter]));
  $("#documents-table").innerHTML = state.documents
    .map((doc) => {
      const level = doc.status === "indexed" ? "" : doc.status === "error" ? "error" : "warning";
      return `<tr>
        <td><strong>${escapeHtml(doc.filename)}</strong><div class="meta">${escapeHtml(doc.type)} - ${escapeHtml(doc.responsible || "Non assigne")}</div></td>
        <td>${escapeHtml(matterById[doc.matter_id]?.name || "Affaire inconnue")}</td>
        <td><span class="badge ${level}">${escapeHtml(statusLabel(doc.status))}</span></td>
        <td>${escapeHtml(qualityLabel(doc.quality))} (${Math.round((doc.confidence || 0) * 100)}%)${doc.message ? `<div class="meta">${escapeHtml(doc.message)}</div>` : ""}</td>
        <td>${doc.page_count || 0}</td>
        <td><div class="actions">
          <button class="secondary" data-open-doc="${escapeAttr(doc.id)}">Texte</button>
          <button class="secondary" data-reprocess-doc="${escapeAttr(doc.id)}">Retraiter OCR</button>
          <a class="badge" href="/api/documents/${escapeAttr(doc.id)}/download" target="_blank" rel="noreferrer">Ouvrir</a>
        </div></td>
      </tr>`;
    })
    .join("");
}

function renderTasks() {
  const matterById = Object.fromEntries(state.matters.map((matter) => [matter.id, matter]));
  $("#tasks-list").innerHTML =
    state.tasks
      .filter((task) => task.status === "open")
      .map(
        (task) => `<div class="stack-item">
          <h4>${escapeHtml(task.title)}</h4>
          <div class="meta">${escapeHtml(matterById[task.matter_id]?.name || "Affaire inconnue")} - ${escapeHtml(task.assignee || "Non assigne")} - ${escapeHtml(task.due_date || "Sans echeance")}</div>
        </div>`,
      )
      .join("") || `<div class="stack-item">Aucune tache ouverte.</div>`;
}

function renderAlerts() {
  $("#alerts-list").innerHTML =
    state.alerts
      .map(
        (alert) => `<div class="stack-item">
          <h4>${escapeHtml(alert.level.toUpperCase())}</h4>
          <div>${escapeHtml(alert.message)}</div>
          <div class="meta">${escapeHtml(alert.created_at.slice(0, 10))}</div>
        </div>`,
      )
      .join("") || `<div class="stack-item">Aucune alerte.</div>`;
}

async function submitJson(form, path) {
  const data = Object.fromEntries(new FormData(form).entries());
  await api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  form.reset();
  await loadState();
}

$("#client-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitJson(event.currentTarget, "/api/clients");
});

$("#matter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitJson(event.currentTarget, "/api/matters");
});

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitJson(event.currentTarget, "/api/tasks");
});

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submitButton = form.querySelector('button[type="submit"]');
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120000);
  submitButton.disabled = true;
  submitButton.textContent = "Traitement...";
  $("#upload-feedback").textContent = "Import du fichier, controle de lisibilite et indexation...";
  try {
    await fetch("/api/documents", {
      method: "POST",
      body: new FormData(form),
      signal: controller.signal,
    }).then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Import impossible");
      return payload;
    });
    form.reset();
    $("#upload-feedback").textContent = "Document importe. Son statut est visible dans le tableau.";
    await loadState();
  } catch (error) {
    const message = error.name === "AbortError" ? "Traitement trop long. Le serveur peut continuer, actualisez dans quelques secondes." : error.message;
    $("#upload-feedback").textContent = message;
  } finally {
    clearTimeout(timeoutId);
    submitButton.disabled = false;
    submitButton.textContent = "Importer et indexer";
  }
});

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(event.currentTarget));
  const payload = await api(`/api/search?${params.toString()}`);
  renderResults(payload.results);
});

$("#documents-table").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-open-doc]");
  if (!button) return;
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Chargement...";
  try {
    const payload = await api(`/api/documents/${button.dataset.openDoc}/text`);
    openTextDialog(
      payload.document.filename,
      payload.pages.map(renderPageText).join("") ||
        "Aucun texte indexe pour ce document.",
    );
  } catch (error) {
    openTextDialog("Texte indisponible", `<p>${escapeHtml(error.message)}</p>`);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

$("#documents-table").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-reprocess-doc]");
  if (!button) return;
  button.disabled = true;
  button.textContent = "OCR...";
  try {
    await api(`/api/documents/${button.dataset.reprocessDoc}/reprocess`, { method: "POST" });
    await loadState();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Retraiter OCR";
  }
});

$("#close-dialog").addEventListener("click", closeTextDialog);
$("#refresh-btn").addEventListener("click", loadState);

function openTextDialog(title, content) {
  const dialog = $("#text-dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-content").innerHTML = content;
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
    return;
  }
  dialog.setAttribute("open", "");
}

function closeTextDialog() {
  const dialog = $("#text-dialog");
  if (typeof dialog.close === "function") {
    dialog.close();
    return;
  }
  dialog.removeAttribute("open");
}

function renderResults(results) {
  $("#search-results").innerHTML =
    results
      .map(
        (result) => `<article class="result">
          <h3>${escapeHtml(result.filename)}</h3>
          <div class="meta">${escapeHtml(result.client)} - ${escapeHtml(result.matter)} - page ${result.page_number} - pertinence ${Math.round(result.score * 100)}%</div>
          <p>${escapeHtml(result.excerpt)}</p>
          <div class="actions">
            <a class="badge" href="/api/documents/${escapeAttr(result.document_id)}/download" target="_blank" rel="noreferrer">Ouvrir le document</a>
          </div>
        </article>`,
      )
      .join("") || `<div class="result">Aucun resultat dans les dossiers autorises.</div>`;
}

function renderPageText(page) {
  const corrected = page.text || "";
  const raw = page.raw_text || "";
  const rawBlock =
    raw && raw !== corrected
      ? `<details><summary>Voir le texte OCR brut</summary><p>${escapeHtml(raw)}</p></details>`
      : "";
  return `<h4>Page ${page.page_number}</h4><p>${escapeHtml(corrected)}</p>${rawBlock}`;
}

function statusLabel(value) {
  return {
    imported: "Importe",
    ocr_pending: "OCR requis",
    ocr_processing: "OCR en cours",
    indexed: "Indexe",
    error: "Erreur",
    low_quality: "Lisibilite faible",
  }[value] || value;
}

function qualityLabel(value) {
  return {
    good: "Bonne",
    medium: "Moyenne",
    low: "Faible",
    unknown: "Inconnue",
    error: "Erreur",
    unsupported: "Non pris en charge",
  }[value] || value;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

loadState().catch((error) => {
  document.body.innerHTML = `<main class="section"><div class="panel">Erreur de chargement: ${escapeHtml(error.message)}</div></main>`;
});
