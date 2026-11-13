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
  review: null, reviewPath: "", reviewing: false,
  veganPlan: null,
  adaptation: null, adaptRequest: 0, previewRequest: 0, busy: false,
  previewReading: "A",
  previews: new Map(),
};

function currentRecipe() {
  return state.review?.recipe ?? state.report.recipe;
}

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
  if ((screen !== "import" && !state.report) || (screen === "adapt" && state.reviewing)) return;
  state.screen = screen;
  for (const name of ["import", "review", "adapt"]) $(`#${name}`).hidden = name !== screen;
  for (const button of all(".nav-item")) {
    const name = button.dataset.screen;
    button.disabled = (name !== "import" && !state.report) || (name === "adapt" && state.reviewing);
    if (name === screen) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  $("#workspace-name").textContent = state.filename || "Recipe workspace";
  if (screen === "review") {
    renderReview();
    renderFieldReview();
  }
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
  const settings = state.example ? report.example_settings : null;
  const title = clean(value(report.recipe.title));
  state.filename = file?.name || (title ? `${title} example` : "Recipe example");
  state.pages = pageCount(report);
  state.page = 1;
  state.selected = 0;
  state.tab = "ingredients";
  state.review = null;
  state.reviewPath = "";
  state.reviewing = false;
  state.veganPlan = report.vegan_plan ?? null;
  state.adaptation = null;
  state.adaptRequest++;
  state.previewRequest++;
  state.previews.clear();

  updateOriginalServings(true);
  $("#target-servings").value = settings?.servings ?? "";
  $("#vegan").setAttribute("aria-checked", String(settings?.vegan === true && hasVeganPlan()));
  renderVeganOption();
  message("#field-review-status");
  show("review");
}

function updateOriginalServings(reset = false) {
  const source = currentRecipe().servings;
  const exampleBaseline = state.example ? state.report.example_settings?.original_servings : null;
  const known = ["stated", "reviewed"].includes(source?.status) && /^(?:(?:serves|makes|yield|für|rende|para)\s+)?([1-9]\d*)(?:\s+(?:servings?|people|persons?|portions?|porç(?:ão|ões)|pessoas?|portionen?|personen?))?[.:]?$/i.exec(clean(value(source)));
  const original = $("#original-servings");
  if (known || reset || original.readOnly) {
    original.value = known?.[1] ?? exampleBaseline ?? "";
  }
  original.readOnly = Boolean(known);
  $("#original-hint").textContent = known
    ? source.status === "reviewed" ? "Set in Review." : "Stated in the recipe."
    : exampleBaseline ? "Example assumption. The source has no serving count."
      : "Enter the original yield to scale this recipe.";
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
  }, "Reading the recipe and preparing a vegan plan…");
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
  const recipe = currentRecipe();
  const fields = flaggedFields();
  const linkedFindings = new Set(fields.flatMap(({field}) => field.review_item_ids || []));
  const documentFindings = (report.comparison?.review_items || []).filter((item, index) => !linkedFindings.has(index + 1));
  $("#review-title").textContent = clean(value(recipe.title)) || "Untitled recipe";
  $("#review-subtitle").textContent = `${state.filename} · ${countLabel(state.pages, "page")}`;
  const pendingFields = state.review ? state.review.status === "needs_review" : fields.length > 0;
  const reviewNeeded = pendingFields || documentFindings.length > 0 || (!state.review && report.status === "needs_review");
  const status = $("#reading-status");
  status.classList.toggle("warning", reviewNeeded);
  status.classList.toggle("info", !reviewNeeded);
  const label = pendingFields ? "Needs review" : documentFindings.length ? "Document findings"
    : reviewNeeded ? "Needs review" : state.review?.decisions.length ? "Fields reviewed" : "Readings agree";
  status.innerHTML = `${reviewNeeded ? icon("warning") : icon("check", 12)} ${label}`;
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
  const item = currentRecipe()[state.tab][state.selected];
  const field = evidenceField(item);
  const evidence = field?.evidence;
  const ingredient = state.tab === "ingredients";
  const subject = ingredient ? ingredientLabel(item) : stepLabel(item || {}, state.selected);
  const detail = ingredient && field === item?.quantity ? " · Quantity" : "";
  const metadata = [state.filename, evidence?.page ? `Page ${evidence.page}` : "",
    evidence?.source ? `Reading ${evidence.source}` : ""].filter(Boolean).join(" / ");
  $("#open-source").disabled = !evidence && !state.file;
  const amount = ingredient ? ingredientAmount(item) : "";
  const label = field?.status === "reviewed" ? "Reviewed amount" : "Extracted amount";
  $("#evidence").innerHTML = item ? `<p>${escape(ingredient ? subject + detail : metadata)}</p>
    ${evidence ? `<div class="evidence-box">
      ${ingredient ? `<div class="evidence-meta">${escape(metadata)}</div>` : ""}
      <blockquote class="evidence-quote${ingredient ? " ingredient-quote" : ""}">“${escape(evidence.quote)}”</blockquote>
    </div>` : "<p>No source excerpt is available for this field.</p>"}
    ${ingredient ? `<div class="fact-row"><span>${label}</span><strong>${escape(amount || "Not stated")}</strong></div>` : ""}`
    : "<p>Select a recipe item to view its evidence.</p>";
}

function renderComparison() {
  const {comparison, extraction_issues: issues = []} = state.report;
  const recipe = currentRecipe();
  const reviews = comparison?.review_items || [];
  const variations = comparison?.equivalent_variations || [];
  const intro = reviews.length || issues.length
    ? `${countLabel(reviews.length + issues.length, "original finding")}.`
    : (state.review?.status ?? state.report.status) === "needs_review" ? "Some extracted fields need review."
      : state.review?.decisions.length ? "The flagged fields have been reviewed."
      : `Both readings agree on the recipe.${variations.length ? ` ${countLabel(variations.length, "difference")} treated as equivalent.` : ""}`;
  const list = [...reviews, ...issues].map((item) => `<li>
    ${item.page ? `<button class="button text" type="button" data-source-page="${escape(item.page)}">Page ${escape(item.page)}</button>` : ""}
    <strong>${escape(item.section || item.field || "Review")}</strong>
    <p>${escape(item.reason)}</p>
    ${["A", "B"].filter((source) => item[`${source.toLowerCase()}_excerpt`]).map((source) =>
      `<blockquote><span class="evidence-meta">Reading ${source}</span><br>${escape(item[`${source.toLowerCase()}_excerpt`])}</blockquote>`).join("")}
  </li>`).join("");
  $("#comparison").innerHTML = `<p>${escape(intro)}</p>
    ${list ? `<ul class="comparison-list">${list}</ul>` : ""}
    <div class="fact-row"><strong>Original servings</strong><span>${escape(value(recipe.servings) || (recipe.servings?.status === "needs_review" ? "Needs review" : "Not stated"))}</span></div>`;
}

const reviewFieldLabels = {
  title: "Title", servings: "Original servings", name: "Name", quantity: "Quantity", unit: "Unit",
  preparation: "Preparation", optional_when: "Optionality", instruction: "Instruction",
  duration: "Duration", condition: "Condition",
};

function flaggedFields() {
  const original = state.report.recipe;
  const fields = [];
  function add(item, names, path = "", label = "") {
    for (const name of names) {
      const field = item[name];
      if (field?.status === "needs_review") {
        fields.push({path: `${path}/${name}`, field, label: [label, reviewFieldLabels[name]].filter(Boolean).join(" · ")});
      }
    }
  }
  add(original, ["title", "servings"]);
  original.ingredients.forEach((item, index) => add(item, ["name", "quantity", "unit", "preparation", "optional_when"],
    `/ingredients/${index}`, clean(value(currentRecipe().ingredients[index].name)) || `Ingredient ${index + 1}`));
  original.tools.forEach((item, index) => add(item, ["name"], `/tools/${index}`,
    clean(value(currentRecipe().tools[index].name)) || `Utensil ${index + 1}`));
  original.steps.forEach((item, index) => add(item, ["instruction", "duration", "condition"],
    `/steps/${index}`, stepLabel(item, index)));
  return fields;
}

function renderFieldReview() {
  const fields = flaggedFields();
  $("#field-review").hidden = fields.length === 0;
  if (!fields.length) return;

  const decisions = new Map((state.review?.decisions || []).map((decision) => [decision.path, decision]));
  const selected = fields.find(({path}) => path === state.reviewPath)
    || fields.find(({path}) => !decisions.has(path)) || fields[0];
  state.reviewPath = selected.path;
  const decision = decisions.get(selected.path);
  const select = $("#field-review-path");
  select.innerHTML = fields.map(({path, label}) =>
    `<option value="${escape(path)}">${escape(label)}${decisions.has(path) ? " · Reviewed" : ""}</option>`).join("");
  select.value = selected.path;
  $("#field-review-count").textContent = `${decisions.size} of ${fields.length} reviewed`;
  $("#field-review-reopen").hidden = !decision;
  $("#field-review-value").value = decision?.value ?? value(selected.field);
  $("#field-review-hint").textContent = selected.path === "/servings"
    ? "Enter the original serving count as a positive whole number."
    : "Confirm a reading after checking the source, or save a correction.";

  const evidence = selected.field.evidence;
  const related = (selected.field.review_item_ids || [])
    .map((id) => state.report.comparison?.review_items[id - 1]).filter(Boolean);
  const issue = state.report.extraction_issues?.find(({field}) => field === selected.path);
  const page = evidence?.page || related.find((item) => item.page)?.page || 1;
  $("#field-review-source").innerHTML = `
    ${decision ? `<p><strong>${decision.action === "confirm" ? "Confirmed" : "Corrected"}</strong> · ${escape(decision.value)}</p>` : ""}
    ${value(selected.field) ? `<p>Original extracted value · ${escape(value(selected.field))}</p>` : ""}
    ${evidence ? `<div class="evidence-box">
      <div class="evidence-meta">Original evidence${evidence.page ? ` · Page ${escape(evidence.page)}` : ""}${evidence.source ? ` · Reading ${escape(evidence.source)}` : ""}</div>
      <blockquote class="evidence-quote">${escape(evidence.quote)}</blockquote>
    </div>` : "<p>No original evidence was verified for this field.</p>"}
    ${related.map((item) => `<div class="evidence-box"><p>${escape(item.reason)}</p>
      ${["A", "B"].filter((source) => item[`${source.toLowerCase()}_excerpt`]).map((source) =>
        `<div class="evidence-meta">Reading ${source}</div><blockquote class="evidence-quote">${escape(item[`${source.toLowerCase()}_excerpt`])}</blockquote>`).join("")}
      </div>`).join("")}
    ${issue ? `<p>${escape(issue.reason)}</p>` : ""}
    <button type="button" class="button text" data-source-page="${escape(page)}" data-source-reading="${escape(evidence?.source || "A")}">Open source page</button>`;
}

function setReviewBusy(busy) {
  state.reviewing = busy;
  for (const control of all("#field-review-form select, #field-review-form textarea, #field-review-form button, button[data-screen='adapt']")) {
    control.disabled = busy;
  }
}

function hasVeganPlan() {
  return ["ready", "needs_review"].includes(state.veganPlan?.status);
}

function renderVeganOption() {
  const control = $("#vegan");
  const available = hasVeganPlan();
  control.disabled = !available;
  if (!available) control.setAttribute("aria-checked", "false");
  const hint = available
    ? state.veganPlan.status === "needs_review" ? "Some substitutions require review." : "The plan is ready to apply."
    : state.veganPlan?.reason || "No vegan plan is available for this recipe.";
  message("#vegan-plan-status", hint);
}

async function saveFieldReview(action) {
  if (state.reviewing) return;
  const report = state.report;
  const path = state.reviewPath;
  const verified = $("#field-review-value").value.trim();
  if (action && !verified) {
    message("#field-review-status", "Enter the verified value.", true);
    return;
  }
  const decisions = (state.review?.decisions || [])
    .filter((item) => item.path !== path)
    .map(({path, action, value}) => ({path, action, value}));
  if (action) decisions.push({path, action, value: verified});

  setReviewBusy(true);
  message("#field-review-status", state.veganPlan && !state.example
    ? "Saving review and checking the vegan plan…" : "Saving review…");
  try {
    const result = await request("/api/review", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({report: {
        recipe: report.recipe,
        vegan_plan: state.veganPlan,
        example: state.example,
      }, decisions}),
    });
    if (report !== state.report) return;
    state.review = result.review;
    state.veganPlan = result.vegan_plan ?? null;
    state.adaptation = null;
    state.adaptRequest++;
    updateOriginalServings();
    renderVeganOption();
    renderReview();
    renderFieldReview();
    message("#field-review-status", action === "confirm" ? "Value confirmed."
      : action === "correct" ? "Correction saved." : "Field reopened for review.");
  } catch (error) {
    if (report === state.report) message("#field-review-status", error.message, true);
  } finally {
    if (report === state.report) setReviewBusy(false);
  }
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
  const vegan = hasVeganPlan() && $("#vegan").getAttribute("aria-checked") === "true";
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
        report: {
          recipe: state.report.recipe,
          ...(state.review ? {review: {decisions: state.review.decisions}} : {}),
          ...(vegan ? {vegan_plan: state.veganPlan} : {}),
        },
        servings,
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
  const item = currentRecipe()[match[1]][Number(match[2])];
  if (match[1] === "ingredients") return ingredientLabel(item);
  if (match[1] === "tools") return clean(value(item?.name)) || "Utensil";
  return stepLabel(item || {}, Number(match[2]));
}

function renderAdaptation() {
  const original = currentRecipe();
  const adapted = state.adaptation;
  const title = clean(value(original.title)) || "recipe";
  $("#adapt-title").textContent = `Adapt ${title}`;
  const target = adapted?.scale?.target_servings;
  const adaptationLabel = adapted?.vegan_requested
    ? adapted.status === "needs_review" ? "Vegan proposal · Review needed" : "Vegan proposal"
    : "Recipe adaptation";
  $("#adapt-subtitle").textContent = [adaptationLabel,
    target ? countLabel(target, "serving") : ""].filter(Boolean).join(" · ");
  const factor = adapted?.scale?.factor;
  $("#target-hint").textContent = factor ? `Serving scale factor: ${factor}.` : "Enter a target to scale quantities.";
  const changes = adapted?.changes || [];
  const changeReasons = new Map(changes.map(({path, reason}) => [path, reason]));
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
    const reason = showName ? changeReasons.get(`/ingredients/${index}/name`) : null;
    return `<tr><th scope="row">${escape(beforeName)}</th><td>${escape(before || "—")}</td>
      <td class="changed-value">${escape(after || "—")}${reason ? `<small class="change-reason">${escape(reason)}</small>` : ""}</td></tr>`;
  }).join("");
  $("#changes").innerHTML = rows ? table(["Ingredient", "Original", "Adapted"], rows)
    : `<p class="empty">${adapted ? "No ingredient changes." : hasVeganPlan()
      ? "Adjust the servings or enable the vegan plan to see ingredient changes."
      : "Adjust the servings to see ingredient changes."}</p>`;
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
  const report = {...state.report};
  if (state.review) report.review = state.review;
  if (state.veganPlan) report.vegan_plan = state.veganPlan;
  else delete report.vegan_plan;
  if (state.adaptation) report.adaptation = state.adaptation;
  const blob = new Blob([JSON.stringify(report, null, 2) + "\n"], {type: "application/json"});
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = (state.filename.replace(/\.[^.]+$/, "") || "recipe") + (state.adaptation ? "-adapted" : "") + ".json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

async function openPage(page, reading = state.previewReading) {
  const dialog = $("#source-dialog");
  if (!dialog.open) dialog.showModal();
  state.previewReading = reading;
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
    const text = state.report.transcriptions[reading] || "";
    const excerpt = text.split(/^\[page: \d+\]\s*$/m)[wanted]?.trim() || text.trim();
    content.innerHTML = `<p>Example transcription · Reading ${escape(reading)}</p><pre class="preview-text">${escape(excerpt)}</pre>`;
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
  if (button?.dataset.sourcePage) openPage(button.dataset.sourcePage, button.dataset.sourceReading || "A");
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
  const item = currentRecipe()[state.tab][state.selected];
  const evidence = evidenceField(item)?.evidence;
  openPage(evidence?.page || 1, evidence?.source || "A");
});
$("#field-review-path").addEventListener("change", (event) => {
  state.reviewPath = event.target.value;
  renderFieldReview();
  message("#field-review-status");
});
$("#field-review-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveFieldReview(event.submitter?.value || "confirm");
});
$("#field-review-reopen").addEventListener("click", () => saveFieldReview(null));
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
