const $ = (selector) => document.querySelector(selector);
const all = (selector) => [...document.querySelectorAll(selector)];
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));
const icon = (name, size = 15) => `<img src="/assets/images/${name}.svg" width="${size}" height="${size}" alt="">`;
const value = (field) => field?.value ?? "";
const clean = (text) => String(text ?? "").replace(/(?<=\p{L})-\s*\r?\n\s*(?=\p{L})/gu, "").replace(/\s+/g, " ").trim();
const countLabel = (number, name) => `${number} ${name}${number === 1 ? "" : "s"}`;

const state = {
  screen: "import", file: null, filename: "", report: null, example: false,
  tab: "ingredients", selected: 0, pages: 1, page: 1,
  adaptation: null, adaptRequest: 0, previewRequest: 0, busy: false,
  previews: new Map(),
};

async function request(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "The request could not be completed.");
  return body;
}

function message(id, text = "", error = false) {
  const status = $(id);
  status.hidden = !text;
  status.textContent = text;
  status.classList.toggle("error", error);
}

function show(screen) {
  if (screen !== "import" && !state.report) return;
  state.screen = screen;
  for (const name of ["import", "review", "adapt"]) $(`#${name}`).hidden = name !== screen;
  for (const button of all(".nav-item")) {
    const name = button.dataset.screen;
    button.disabled = name !== "import" && !state.report;
    if (name === screen) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  $("#workspace-name").textContent = state.filename || "Recipe workspace";
  if (screen === "review") renderReview();
  if (screen === "adapt") {
    renderAdaptation();
    updateAdaptation();
  }
  window.scrollTo(0, 0);
}

function pageCount(report) {
  const markers = Object.values(report.transcriptions || {}).flatMap(
    (text) => [...text.matchAll(/^\[page: (\d+)\]/gm)].map((match) => Number(match[1])),
  );
  return Math.max(1, ...markers);
}

function loadReport(report, file) {
  state.report = report;
  state.file = file;
  state.example = !file;
  state.filename = file?.name || "Sauerbraten example";
  state.pages = pageCount(report);
  state.page = 1;
  state.selected = 0;
  state.tab = "ingredients";
  state.adaptation = null;
  state.adaptRequest++;
  state.previews.clear();

  const source = report.recipe.servings;
  const stated = source?.status === "stated" && /^(?:(?:serves|makes|yield|für|rende|para)\s+)?([1-9]\d*)(?:\s+(?:servings?|people|persons?|portions?|porç(?:ão|ões)|pessoas?|portionen?|personen?))?[.:]?$/i.exec(clean(value(source)));
  const original = $("#original-servings");
  original.value = stated?.[1] || (state.example ? 4 : "");
  original.readOnly = Boolean(stated);
  $("#original-hint").textContent = stated
    ? "Stated in the recipe."
    : state.example ? "Example assumption. The source has no serving count."
      : "Enter the original yield to scale this recipe.";
  $("#target-servings").value = state.example ? 6 : "";
  $("#vegan").setAttribute("aria-checked", String(state.example));
  show("review");
}

async function importRecipe(file) {
  if (!file || state.busy) return;
  if (!/\.(pdf|png|jpe?g)$/i.test(file.name)) {
    message("#import-status", "Choose a PDF, PNG or JPEG file.", true);
    return;
  }
  if (!file.size || file.size > 14_000_000) {
    message("#import-status", "Choose a non-empty file no larger than 14 MB.", true);
    return;
  }
  await runImport(async () => {
    const report = await request(`/api/extract?filename=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file,
    });
    loadReport(report, file);
  }, "Reading the recipe and comparing both transcriptions…");
}

async function runImport(action, progress) {
  if (state.busy) return;
  state.busy = true;
  $("#choose-file").disabled = true;
  $("#try-example").disabled = true;
  $("#drop-zone").setAttribute("aria-busy", "true");
  message("#import-status", progress);
  try {
    await action();
    message("#import-status");
  } catch (error) {
    message("#import-status", error.message, true);
  } finally {
    state.busy = false;
    $("#choose-file").disabled = false;
    $("#try-example").disabled = false;
    $("#drop-zone").removeAttribute("aria-busy");
    $("#recipe-file").value = "";
  }
}

function evidenceField(item) {
  if (state.tab === "steps") return item?.instruction;
  return item?.quantity?.evidence || item?.quantity?.status === "needs_review" ? item.quantity : item?.name;
}

function ingredientLabel(ingredient) {
  return clean(value(ingredient?.name)) || "Unresolved ingredient";
}

function ingredientAmount(ingredient) {
  if (ingredient?.quantity?.status === "needs_review" || ingredient?.unit?.status === "needs_review") {
    return "Needs review";
  }
  return [clean(value(ingredient?.quantity)), clean(value(ingredient?.unit))].filter(Boolean).join(" ");
}

function stepLabel(step, index) {
  return step.display_title || `Step ${index + 1}`;
}

function stepDescription(step) {
  const instruction = clean(value(step.instruction));
  const condition = clean(value(step.condition));
  const duration = clean(value(step.duration));
  return [
    condition && !instruction.includes(condition) ? condition : "",
    instruction || "Instruction needs review.",
    duration && !instruction.includes(duration) ? duration : "",
  ].filter(Boolean).join(" · ");
}

function table(headers, body) {
  return `<div class="table-scroll"><table class="data-table"><thead><tr>
    ${headers.map((header) => `<th scope="col">${escape(header)}</th>`).join("")}
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderReview() {
  const report = state.report;
  const recipe = report.recipe;
  $("#review-title").textContent = clean(value(recipe.title)) || "Untitled recipe";
  $("#review-subtitle").textContent = `${state.filename} · ${countLabel(state.pages, "page")}`;
  const reviewNeeded = report.status === "needs_review";
  const status = $("#reading-status");
  status.classList.toggle("warning", reviewNeeded);
  status.classList.toggle("info", !reviewNeeded);
  status.innerHTML = reviewNeeded ? `${icon("warning")} Needs review` : `${icon("check", 12)} Readings agree`;
  const readings = Object.keys(report.transcriptions || {});
  $("#reading-count").textContent = countLabel(readings.length, "reading");

  for (const button of all(".tab")) {
    const tab = button.dataset.tab;
    const active = state.tab === tab;
    button.textContent = `${tab === "ingredients" ? "Ingredients" : "Steps"} · ${recipe[tab].length}`;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  $("#review-list").setAttribute("aria-labelledby", `${state.tab}-tab`);
  if (state.tab === "ingredients") {
    const rows = recipe.ingredients.map((item, index) => {
      const field = evidenceField(item);
      const page = field?.evidence?.page;
      const preparation = clean(value(item.preparation)) || (value(item.optional_when) ? "Optional" : "—");
      return `<tr class="${index === state.selected ? "selected" : ""}" data-row="${index}">
        <th scope="row"><button type="button" class="row-button" data-select="${index}" aria-pressed="${index === state.selected}">${escape(ingredientLabel(item))}</button></th>
        <td>${escape(ingredientAmount(item) || "—")}</td>
        <td>${escape(preparation)}</td>
        <td>${page ? `Page ${escape(page)}` : "—"}</td>
      </tr>`;
    }).join("");
    $("#review-list").innerHTML = `<div class="section-head"><h2>Extracted ingredients</h2>
      <span>${countLabel(recipe.ingredients.length, "ingredient")}</span></div>
      ${rows ? table(["Ingredient", "Amount", "Preparation", "Source"], rows) : '<p class="empty">No ingredients were extracted.</p>'}`;
  } else {
    $("#review-list").innerHTML = `<div class="section-head"><h2>Cooking steps</h2>
      <span class="badge">${state.pages > 1 ? `Pages 1–${state.pages}` : "Page 1"}</span></div>
      <ol class="step-list">${recipe.steps.map((step, index) => `<li>
        <button type="button" class="step-row ${index === state.selected ? "selected" : ""}" data-select="${index}" aria-pressed="${index === state.selected}">
          <span class="step-number">${index + 1}</span>
          <span class="step-copy"><strong class="step-title">${escape(stepLabel(step, index))}</strong>
            <span>${escape(stepDescription(step))}</span></span>
        </button></li>`).join("")}</ol>
      ${recipe.steps.length ? "" : '<p class="empty">No steps were extracted.</p>'}`;
  }
  renderEvidence();
  renderComparison();
}

function renderEvidence() {
  const item = state.report.recipe[state.tab][state.selected];
  const field = evidenceField(item);
  const evidence = field?.evidence;
  const ingredient = state.tab === "ingredients";
  const subject = ingredient ? ingredientLabel(item) : stepLabel(item || {}, state.selected);
  const detail = ingredient && field === item?.quantity ? " · Quantity" : "";
  const metadata = [state.filename, evidence?.page ? `Page ${evidence.page}` : "",
    evidence?.source ? `Reading ${evidence.source}` : ""].filter(Boolean).join(" / ");
  $("#open-source").disabled = !evidence && !state.file;
  const amount = ingredient ? ingredientAmount(item) : "";
  $("#evidence").innerHTML = item ? `<p>${escape(ingredient ? subject + detail : metadata)}</p>
    ${evidence ? `<div class="evidence-box">
      ${ingredient ? `<div class="evidence-meta">${escape(metadata)}</div>` : ""}
      <blockquote class="evidence-quote${ingredient ? " ingredient-quote" : ""}">“${escape(evidence.quote)}”</blockquote>
    </div>` : "<p>No source excerpt is available for this field.</p>"}
    ${ingredient ? `<div class="fact-row"><span>Extracted amount</span><strong>${escape(amount || "Not stated")}</strong></div>` : ""}`
    : "<p>Select a recipe item to view its evidence.</p>";
}

function renderComparison() {
  const {recipe, comparison, extraction_issues: issues = []} = state.report;
  const reviews = comparison?.review_items || [];
  const variations = comparison?.equivalent_variations || [];
  const intro = reviews.length || issues.length
    ? `${countLabel(reviews.length + issues.length, "item")} to review.`
    : state.report.status === "needs_review" ? "Some extracted fields need review."
      : `Both readings agree on the recipe.${variations.length ? ` ${countLabel(variations.length, "difference")} treated as equivalent.` : ""}`;
  const list = [...reviews, ...issues].map((item) => `<li>
    ${item.page ? `<button class="button text" type="button" data-source-page="${escape(item.page)}">Page ${escape(item.page)}</button>` : ""}
    <strong>${escape(item.section || item.field || "Review")}</strong>
    <p>${escape(item.reason)}</p>
    ${["a_excerpt", "b_excerpt"].filter((key) => item[key]).map((key) => `<blockquote>${escape(item[key])}</blockquote>`).join("")}
  </li>`).join("");
  $("#comparison").innerHTML = `<p>${escape(intro)}</p>
    ${list ? `<ul class="comparison-list">${list}</ul>` : ""}
    <div class="fact-row"><strong>Original servings</strong><span>${escape(value(recipe.servings) || (recipe.servings?.status === "needs_review" ? "Needs review" : "Not stated"))}</span></div>`;
}

function quantityField(input) {
  return input.value === "" ? null : Number(input.value);
}

let adaptationTimer;
function scheduleAdaptation() {
  window.clearTimeout(adaptationTimer);
  state.adaptRequest++;
  $("#export-recipe").disabled = true;
  adaptationTimer = window.setTimeout(updateAdaptation, 220);
}

async function updateAdaptation() {
  if (state.screen !== "adapt") return;
  const sequence = ++state.adaptRequest;
  const original = $("#original-servings");
  const target = $("#target-servings");
  const servings = quantityField(target);
  const baseline = original.readOnly ? null : quantityField(original);
  const vegan = $("#vegan").getAttribute("aria-checked") === "true";
  const invalid = !target.checkValidity() || (servings !== null && !original.checkValidity());
  if (invalid || (servings !== null && !original.readOnly && baseline === null)) {
    state.adaptation = null;
    renderAdaptation();
    message("#adapt-status", invalid ? "Enter positive whole numbers for servings." : "Enter the original servings to scale the recipe.", true);
    $("#export-recipe").disabled = true;
    return;
  }
  if (servings === null && !vegan) {
    state.adaptation = null;
    renderAdaptation();
    message("#adapt-status");
    $("#export-recipe").disabled = false;
    return;
  }
  $("#export-recipe").disabled = true;
  message("#adapt-status", "Updating recipe…");
  try {
    const report = await request("/api/adapt", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        report: {recipe: state.report.recipe}, servings,
        original_servings: servings === null ? null : baseline, vegan,
      }),
    });
    if (sequence !== state.adaptRequest) return;
    state.adaptation = report.adaptation;
    renderAdaptation();
    message("#adapt-status");
    $("#export-recipe").disabled = false;
  } catch (error) {
    if (sequence !== state.adaptRequest) return;
    state.adaptation = null;
    renderAdaptation();
    message("#adapt-status", error.message, true);
  }
}

function reviewLabel(path) {
  const match = /^\/(ingredients|tools|steps)\/(\d+)/.exec(path);
  if (!match) return "Recipe";
  const item = state.report.recipe[match[1]][Number(match[2])];
  if (match[1] === "ingredients") return ingredientLabel(item);
  if (match[1] === "tools") return clean(value(item?.name)) || "Utensil";
  return stepLabel(item || {}, Number(match[2]));
}

function renderAdaptation() {
  const original = state.report.recipe;
  const adapted = state.adaptation;
  const title = clean(value(original.title)) || "recipe";
  $("#adapt-title").textContent = `Adapt ${title}`;
  const target = adapted?.scale?.target_servings;
  $("#adapt-subtitle").textContent = [adapted?.vegan_requested ? "Vegan version" : "Recipe adaptation",
    target ? countLabel(target, "serving") : ""].filter(Boolean).join(" · ");
  const factor = adapted?.scale?.factor;
  $("#target-hint").textContent = factor ? `Serving scale factor: ${factor}.` : "Enter a target to scale quantities.";
  const changes = adapted?.changes || [];
  $("#change-count").textContent = `${changes.length} total`;
  const rows = original.ingredients.flatMap((item, index) => {
    const changed = adapted?.recipe.ingredients[index];
    if (!changed) return [];
    const beforeName = ingredientLabel(item);
    const afterName = ingredientLabel(changed);
    const beforeAmount = ingredientAmount(item);
    const afterAmount = ingredientAmount(changed);
    if (beforeName === afterName && beforeAmount === afterAmount) return [];
    const showName = beforeName !== afterName;
    const before = showName ? [beforeName, beforeAmount].filter(Boolean).join(" · ") : beforeAmount;
    const after = showName ? [afterName, afterAmount].filter(Boolean).join(" · ") : afterAmount;
    return `<tr><th scope="row">${escape(beforeName)}</th><td>${escape(before || "—")}</td>
      <td class="changed-value">${escape(after || "—")}</td></tr>`;
  }).join("");
  $("#changes").innerHTML = rows ? table(["Ingredient", "Original", "Adapted"], rows)
    : '<p class="empty">Adjust the servings or enable the vegan version to see ingredient changes.</p>';
  const reviews = adapted?.review_items || [];
  const banner = $("#adaptation-review");
  banner.hidden = !reviews.length;
  banner.innerHTML = reviews.length ? `<h2>${icon("warning")} ${countLabel(reviews.length, "item")} to review</h2>
    <ul>${reviews.map((item) => `<li><strong>${escape(reviewLabel(item.path))}</strong> · ${escape(item.reason)}</li>`).join("")}</ul>` : "";
  const steps = (adapted?.recipe.steps || []).flatMap((step, index) => {
    const old = original.steps[index];
    const updated = ["instruction", "condition", "duration"].some((field) => value(step[field]) !== value(old?.[field]));
    return updated ? [`<li><strong>${escape(stepLabel(step, index))}</strong><p>${escape(stepDescription(step))}</p></li>`] : [];
  });
  $("#adapted-step-card").hidden = !steps.length;
  $("#adapted-steps").innerHTML = `<ul class="comparison-list">${steps.join("")}</ul>`;
}

function exportReport() {
  const report = state.adaptation ? {...state.report, adaptation: state.adaptation} : state.report;
  const blob = new Blob([JSON.stringify(report, null, 2) + "\n"], {type: "application/json"});
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = (state.filename.replace(/\.[^.]+$/, "") || "recipe") + (state.adaptation ? "-adapted" : "") + ".json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

async function openPage(page) {
  const dialog = $("#source-dialog");
  if (!dialog.open) dialog.showModal();
  state.page = Math.min(state.pages, Math.max(1, Number(page) || 1));
  const wanted = state.page;
  const sequence = ++state.previewRequest;
  const content = $("#source-content");
  const updateControls = () => {
    $("#source-page").textContent = `Page ${wanted} of ${state.pages}`;
    $("#previous-page").disabled = wanted <= 1;
    $("#next-page").disabled = wanted >= state.pages;
  };
  updateControls();
  if (!state.file) {
    const item = state.report.recipe[state.tab][state.selected];
    const label = evidenceField(item)?.evidence?.source || "A";
    const reading = state.report.transcriptions[label] || "";
    const excerpt = reading.split(/^\[page: \d+\]\s*$/m)[wanted]?.trim() || reading.trim();
    content.innerHTML = `<p>Example transcription · Reading ${escape(label)}</p><pre class="preview-text">${escape(excerpt)}</pre>`;
    return;
  }
  content.textContent = "Loading source page…";
  try {
    let result = state.previews.get(wanted);
    if (!result) {
      result = await request(`/api/preview?filename=${encodeURIComponent(state.file.name)}&page=${wanted}`, {
        method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: state.file,
      });
      if (sequence !== state.previewRequest) return;
      state.previews.set(wanted, result);
    }
    state.pages = result.page_count;
    updateControls();
    const image = document.createElement("img");
    image.className = "preview-image";
    image.src = result.image;
    image.alt = `Page ${wanted} of ${state.filename}`;
    content.replaceChildren(image);
  } catch (error) {
    if (sequence === state.previewRequest) content.textContent = error.message;
  }
}

$("#choose-file").addEventListener("click", () => $("#recipe-file").click());
$("#recipe-file").addEventListener("change", (event) => importRecipe(event.target.files[0]));
$("#try-example").addEventListener("click", () => runImport(async () => {
  loadReport(await request("/assets/example.json"), null);
}, "Loading the example…"));
const dropZone = $("#drop-zone");
for (const eventName of ["dragover", "dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.toggle("is-dragging", eventName === "dragover" && !state.busy);
    if (eventName === "drop") importRecipe(event.dataTransfer.files[0]);
  });
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-screen],button[data-tab],button[data-select],button[data-source-page]");
  if (button?.dataset.screen) show(button.dataset.screen);
  if (button?.dataset.tab && button.dataset.tab !== state.tab) {
    state.tab = button.dataset.tab;
    state.selected = 0;
    renderReview();
  }
  const row = event.target.closest("[data-row]");
  const selection = button?.dataset.select ?? row?.dataset.row;
  if (selection !== undefined) {
    state.selected = Number(selection);
    renderReview();
    if (button) $(`[data-select="${selection}"]`)?.focus({preventScroll: true});
  }
  if (button?.dataset.sourcePage) openPage(button.dataset.sourcePage);
});
$(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  show("import");
});
$(".tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const other = $(state.tab === "ingredients" ? "#steps-tab" : "#ingredients-tab");
  other.click();
  other.focus();
});
$("#open-source").addEventListener("click", () => {
  const item = state.report.recipe[state.tab][state.selected];
  openPage(evidenceField(item)?.evidence?.page || 1);
});
$("#previous-page").addEventListener("click", () => openPage(state.page - 1));
$("#next-page").addEventListener("click", () => openPage(state.page + 1));
$("#source-dialog").addEventListener("close", () => { state.previewRequest++; });
$("#adapt-settings").addEventListener("submit", (event) => { event.preventDefault(); updateAdaptation(); });
$("#adapt-settings").addEventListener("input", scheduleAdaptation);
$("#vegan").addEventListener("click", (event) => {
  const control = event.currentTarget;
  control.setAttribute("aria-checked", String(control.getAttribute("aria-checked") !== "true"));
  scheduleAdaptation();
});
$("#export-recipe").addEventListener("click", exportReport);
