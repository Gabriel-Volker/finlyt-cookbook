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
  tab: "ingredients", pages: 1, page: 1,
  review: null, reviewPath: "", reviewing: false,
  scalingPlan: null, veganPlan: null,
  adaptation: null, adaptationReady: false, adaptRequest: 0,
  output: null, outputRequest: 0, reviewOutput: null, reviewOutputRequest: 0,
  localizedOutputs: new Map(), previewRequest: 0, busy: false,
  previewReading: "A",
  previews: new Map(),
};

function currentRecipe() {
  return state.review?.recipe ?? state.report.recipe;
}

function reviewDisplayRecipe() {
  return state.reviewOutput?.recipe ?? currentRecipe();
}

function localizedText(output, key, original) {
  return output?.texts?.[key] ?? original;
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
  const importing = screen === "import";
  const outputControl = $("#output-language-control");
  const workspaceOutput = $("#workspace-output-language");
  const outputSlot = importing ? $("#import-output-language") : workspaceOutput;
  if (outputControl.parentElement !== outputSlot) outputSlot.append(outputControl);
  workspaceOutput.hidden = importing;
  $("#output-language-hint").hidden = !importing;
  message("#output-status");
  if (screen === "review") {
    renderReview();
    updateReviewOutput();
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
  state.tab = "ingredients";
  state.review = null;
  state.reviewOutput = null;
  state.reviewOutputRequest++;
  state.reviewPath = "";
  state.reviewing = false;
  state.scalingPlan = report.scaling_plan ?? null;
  state.veganPlan = report.vegan_plan ?? null;
  state.adaptation = null;
  state.adaptationReady = false;
  state.adaptRequest++;
  state.localizedOutputs = new Map();
  state.previewRequest++;
  state.previews.clear();

  updateOriginalServings(true);
  $("#target-servings").value = settings?.servings ?? "";
  $("#vegan").setAttribute("aria-checked", String(settings?.vegan === true && hasVeganPlan()));
  renderVeganOption();
  clearOutput();
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
  }, "Reading the recipe and preparing adaptation plans…");
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

const reviewTabs = {ingredients: "Ingredients", steps: "Steps", tools: "Utensils"};

function editValue(path, label, text, className = "") {
  return `<button type="button" class="edit-value ${className}" data-edit-field="${escape(path)}"
    aria-label="${escape(label)}" aria-haspopup="dialog">${text}</button>`;
}

function editableText(field, missing = "—") {
  return field.status === "needs_review" ? "Needs review" : clean(value(field)) || missing;
}

function renderReview() {
  const report = state.report;
  const recipe = reviewDisplayRecipe();
  const fields = flaggedFields();
  const linkedFindings = new Set(fields.flatMap(({field}) => field.review_item_ids || []));
  const documentFindings = (report.comparison?.review_items || []).filter((item, index) => !linkedFindings.has(index + 1));
  const documentIssues = (report.extraction_issues || []).some(({field}) => ["/recipe", "/ingredients", "/steps"].includes(field));
  $("#edit-recipe-title").textContent = clean(value(recipe.title)) || "Untitled recipe";
  $("#review-subtitle").textContent = `${state.filename} · ${countLabel(state.pages, "page")}`;
  const pendingFields = state.review ? state.review.status === "needs_review" : fields.length > 0;
  const reviewNeeded = pendingFields || documentIssues || documentFindings.length > 0 || (!state.review && report.status === "needs_review");
  const status = $("#reading-status");
  status.classList.toggle("warning", reviewNeeded);
  status.classList.toggle("info", !reviewNeeded);
  const label = pendingFields || documentIssues ? "Needs review" : documentFindings.length ? "Document findings"
    : reviewNeeded ? "Needs review" : state.review?.decisions.length ? "Review updated" : "Readings agree";
  status.innerHTML = `${reviewNeeded ? icon("warning") : icon("check", 12)} ${label}`;
  const readings = Object.keys(report.transcriptions || {});
  $("#reading-count").textContent = countLabel(readings.length, "reading");

  for (const button of all(".tab")) {
    const tab = button.dataset.tab;
    const active = state.tab === tab;
    button.textContent = `${reviewTabs[tab]} · ${recipe[tab].length}`;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  $("#review-list").setAttribute("aria-labelledby", `${state.tab}-tab`);
  if (state.tab === "ingredients") {
    const rows = recipe.ingredients.map((item, index) => {
      const base = `/ingredients/${index}`;
      const name = ingredientLabel(item);
      const evidence = item.quantity.evidence || item.name.evidence;
      const page = evidence?.page;
      const optional = editableText(item.optional_when, "");
      const quantity = editValue(`${base}/quantity`, `Edit quantity for ${name}`, escape(editableText(item.quantity)));
      const unit = editableText(item.unit, "");
      return `<tr data-row-edit="${base}/quantity">
        <th scope="row">${editValue(`${base}/name`, `Edit name for ${name}`, escape(name))}</th>
        <td><div class="edit-amount">${quantity}${unit ? editValue(`${base}/unit`, `Edit unit for ${name}`, escape(unit)) : ""}</div></td>
        <td>${editValue(`${base}/preparation`, `Edit preparation for ${name}`, escape(editableText(item.preparation)))}
          ${optional ? editValue(`${base}/optional_when`, `Edit optionality for ${name}`, escape(optional)) : ""}</td>
        <td>${page ? `<button type="button" class="edit-value" data-source-page="${escape(page)}"
          data-source-reading="${escape(evidence.source || "A")}">Page ${escape(page)}</button>` : "—"}</td>
      </tr>`;
    }).join("");
    $("#review-list").innerHTML = `<div class="section-head"><h2>Extracted ingredients</h2>
      <span>${countLabel(recipe.ingredients.length, "ingredient")}</span></div>
      ${rows ? table(["Ingredient", "Amount", "Preparation", "Source"], rows) : '<p class="empty">No ingredients were extracted.</p>'}`;
  } else if (state.tab === "steps") {
    $("#review-list").innerHTML = `<div class="section-head"><h2>Cooking steps</h2>
      <span class="badge">${state.pages > 1 ? `Pages 1–${state.pages}` : "Page 1"}</span></div>
      <ol class="step-list">${recipe.steps.map((step, index) => `<li>${editValue(
        `/steps/${index}/instruction`, `Edit ${stepLabel(step, index)}`,
        `
          <span class="step-number">${index + 1}</span>
          <span class="step-copy"><strong class="step-title">${escape(stepLabel(step, index))}</strong>
            <span>${escape(stepDescription(step))}</span></span>`, "step-row",
      )}</li>`).join("")}</ol>
      ${recipe.steps.length ? "" : '<p class="empty">No steps were extracted.</p>'}`;
  } else {
    const rows = recipe.tools.map((tool, index) => {
      const name = editableText(tool.name, `Utensil ${index + 1}`);
      return `<tr><th scope="row">${editValue(`/tools/${index}/name`, `Edit ${name}`, escape(name))}</th></tr>`;
    }).join("");
    $("#review-list").innerHTML = `<div class="section-head"><h2>Utensils</h2></div>
      ${rows ? table(["Name"], rows) : '<p class="empty">No utensils were extracted.</p>'}`;
  }
  renderPendingReview(fields);
  renderComparison();
}

function renderPendingReview(fields) {
  const reviewed = new Set((state.review?.decisions || []).map(({path}) => path));
  const pending = fields.filter(({path}) => !reviewed.has(path));
  $("#pending-review").hidden = !pending.length;
  $("#field-review-count").textContent = String(pending.length);
  $("#pending-review-fields").innerHTML = pending.map(({path, label}) =>
    `<li>${editValue(path, `Edit ${label}`, escape(label))}</li>`).join("");
}

function renderComparison() {
  const {comparison, extraction_issues: issues = []} = state.report;
  const recipe = reviewDisplayRecipe();
  const reviews = comparison?.review_items || [];
  const variations = comparison?.equivalent_variations || [];
  const intro = reviews.length || issues.length
    ? `${countLabel(reviews.length + issues.length, "original finding")}.`
    : (state.review?.status ?? state.report.status) === "needs_review" ? "Some extracted fields need review."
      : state.review?.decisions.length ? "Review decisions saved."
      : `Both readings agree on the recipe.${variations.length ? ` ${countLabel(variations.length, "difference")} treated as equivalent.` : ""}`;
  const list = [...reviews, ...issues].map((item, index) => {
    const page = item.page ?? item.evidence?.page;
    const key = index < reviews.length ? `comparison:${index}` : `extraction:${index - reviews.length}`;
    return `<li>
      ${page ? `<button class="button text" type="button" data-source-page="${escape(page)}" data-source-reading="${escape(item.evidence?.source || "A")}">Page ${escape(page)}</button>` : ""}
      <strong>${escape(localizedText(state.reviewOutput, `${key}:section`, item.section) || item.field || "Review")}</strong>
      <p>${escape(localizedText(state.reviewOutput, key, item.reason))}</p>
      ${item.evidence?.quote ? `<blockquote><span class="evidence-meta">Reading ${escape(item.evidence.source)}</span><br>${escape(item.evidence.quote)}</blockquote>` : ""}
      ${["A", "B"].filter((source) => item[`${source.toLowerCase()}_excerpt`]).map((source) =>
        `<blockquote><span class="evidence-meta">Reading ${source}</span><br>${escape(item[`${source.toLowerCase()}_excerpt`])}</blockquote>`).join("")}
    </li>`;
  }).join("");
  $("#comparison").innerHTML = `<p>${escape(intro)}</p>
    ${list ? `<ul class="comparison-list">${list}</ul>` : ""}
    <div class="fact-row"><strong>Original servings</strong>${editValue("/servings", "Edit original servings",
      escape(editableText(recipe.servings, "Not stated")))}</div>`;
}

const reviewFieldLabels = {
  title: "Title", servings: "Original servings", name: "Name", quantity: "Quantity", unit: "Unit",
  preparation: "Preparation", optional_when: "Optionality", instruction: "Instruction",
  duration: "Duration", condition: "Condition",
  ingredient_ids: "Linked ingredients", tool_ids: "Linked utensils",
};
const reviewOptionalFields = new Set(["title", "servings", "quantity", "unit", "preparation", "optional_when", "duration", "condition"]);
const reviewReferenceFields = {ingredient_ids: "ingredients", tool_ids: "tools"};

function reviewFields() {
  const original = state.report.recipe;
  const recipe = reviewDisplayRecipe();
  const fields = [];
  function add(item, names, label, path = "") {
    for (const name of names) {
      const collection = reviewReferenceFields[name];
      const field = collection ? {value: item[name] || [], status: "stated"} : item[name];
      fields.push({path: `${path}/${name}`, field, name, record: path, subject: label, collection,
        label: [label, reviewFieldLabels[name]].filter(Boolean).join(" · ")});
    }
  }
  add(original, ["title", "servings"], "Recipe");
  original.ingredients.forEach((item, index) => add(item, ["name", "quantity", "unit", "preparation", "optional_when"],
    clean(value(recipe.ingredients[index].name)) || `Ingredient ${index + 1}`, `/ingredients/${index}`));
  original.tools.forEach((item, index) => add(item, ["name"],
    clean(value(recipe.tools[index].name)) || `Utensil ${index + 1}`, `/tools/${index}`));
  original.steps.forEach((item, index) => add(item, ["instruction", "duration", "condition", "ingredient_ids", "tool_ids"],
    stepLabel(item, index), `/steps/${index}`));
  return fields;
}

function flaggedFields() {
  return reviewFields().filter(({field}) => field.status === "needs_review");
}

function renderFieldReview() {
  const fields = reviewFields();
  const selected = fields.find(({path}) => path === state.reviewPath);
  if (!selected) return;
  const decisions = new Map((state.review?.decisions || []).map((decision) => [decision.path, decision]));
  const decision = decisions.get(selected.path);
  const localFields = fields.filter(({record}) => record === selected.record);
  const select = $("#field-review-path");
  select.innerHTML = localFields.map(({path, name, field}) => `<option value="${escape(path)}">
    ${escape(reviewFieldLabels[name])}${decisions.has(path) ? " · Reviewed" : field.status === "needs_review" ? " · Needs review" : ""}</option>`).join("");
  select.value = selected.path;
  $("#field-review-picker").hidden = localFields.length === 1;
  $("#field-review-title").textContent = `Edit ${selected.subject}`;
  $("#field-review-reopen").hidden = !decision;
  $("#field-review-reopen").textContent = selected.field.status === "needs_review" ? "Reopen" : "Undo review";
  const reviewedValue = decision ? decision.value : selected.field.value;
  const isReference = Boolean(selected.collection);
  const sourceValue = Array.isArray(reviewedValue) ? reviewedValue : [];
  $("#field-review-text").hidden = isReference;
  $("#field-review-value").disabled = isReference;
  $("#field-review-links").hidden = !isReference;
  const linked = $("#field-review-references");
  linked.disabled = !isReference;
  linked.innerHTML = isReference ? reviewDisplayRecipe()[selected.collection].map((entry, index) => {
    const name = clean(value(entry.name)) || `${selected.collection === "tools" ? "Utensil" : "Ingredient"} ${index + 1}`;
    return `<option value="${escape(entry.id)}"${sourceValue.includes(entry.id) ? " selected" : ""}>${escape(name)}</option>`;
  }).join("") : "";
  const input = $("#field-review-value");
  input.value = isReference ? "" : reviewedValue ?? "";
  input.rows = selected.name === "instruction" ? 4 : 2;
  $("#field-review-value-label").textContent = reviewFieldLabels[selected.name];
  const needsReview = selected.field.status === "needs_review" && !decision;
  $("#field-review-clear").hidden = !reviewOptionalFields.has(selected.name) || (reviewedValue === null && !needsReview);
  const hint = $("#field-review-hint");
  const translated = state.reviewOutput?.language === "english"
    ? value(selected.path.split("/").slice(1).reduce((node, key) => node?.[key], state.reviewOutput.recipe)) : "";
  hint.textContent = selected.path === "/servings" ? "Enter the original serving count as a positive whole number."
    : translated && translated !== reviewedValue ? "Enter the correction in the source language." : "";
  hint.hidden = !hint.textContent;

  const stepIndex = isReference ? Number(selected.path.split("/")[2]) : null;
  const evidence = isReference ? state.report.recipe.steps[stepIndex].instruction.evidence : selected.field.evidence;
  const describe = (content) => {
    if (!isReference) return content ?? "Not stated";
    const names = new Map(reviewDisplayRecipe()[selected.collection].map((entry) => [entry.id, clean(value(entry.name)) || entry.id]));
    return (Array.isArray(content) ? content : []).map((id) => names.get(id) || id).join(", ") || "None";
  };
  const comparisons = state.report.comparison?.review_items || [];
  const related = (selected.field.review_item_ids || [])
    .flatMap((id) => comparisons[id - 1] ? [{...comparisons[id - 1], key: `comparison:${id - 1}`}] : []);
  const issues = state.report.extraction_issues || [];
  const issueIndex = issues.findIndex(({field}) => field === selected.path);
  const issue = issues[issueIndex];
  const page = evidence?.page || issue?.evidence?.page || related.find((item) => item.page)?.page || 1;
  $("#field-review-source").innerHTML = `
    ${translated && translated !== reviewedValue ? `<p><strong>English reading</strong> · ${escape(translated)}</p>` : ""}
    ${decision ? `<p><strong>${decision.action === "confirm" ? "Confirmed" : "Corrected"}</strong> · ${escape(describe(decision.value))}</p>` : ""}
    <p>Original extracted value · ${escape(describe(selected.field.value))}</p>
    ${evidence ? `<div class="evidence-box">
      <div class="evidence-meta">Original evidence${evidence.page ? ` · Page ${escape(evidence.page)}` : ""}${evidence.source ? ` · Reading ${escape(evidence.source)}` : ""}</div>
      <blockquote class="evidence-quote">${escape(evidence.quote)}</blockquote>
    </div>` : "<p>No original evidence was verified for this field.</p>"}
    ${related.map((item) => `<div class="evidence-box"><p>${escape(localizedText(state.reviewOutput, item.key, item.reason))}</p>
      ${["A", "B"].filter((source) => item[`${source.toLowerCase()}_excerpt`]).map((source) =>
        `<div class="evidence-meta">Reading ${source}</div><blockquote class="evidence-quote">${escape(item[`${source.toLowerCase()}_excerpt`])}</blockquote>`).join("")}
      </div>`).join("")}
    ${issue ? `<p>${escape(localizedText(state.reviewOutput, `extraction:${issueIndex}`, issue.reason))}</p>` : ""}
    <button type="button" class="button text" data-source-page="${escape(page)}" data-source-reading="${escape(evidence?.source || issue?.evidence?.source || "A")}">Open source page</button>`;
}

function focusReviewValue() {
  const input = $("#field-review-value").disabled ? $("#field-review-references") : $("#field-review-value");
  input.focus({preventScroll: true});
}

let reviewTrigger;

function openFieldReview(path, trigger) {
  if (state.reviewing) return;
  const dialog = $("#field-review");
  reviewTrigger = trigger;
  state.reviewPath = path;
  message("#field-review-status");
  renderFieldReview();
  dialog.showModal();
  dialog.scrollTop = 0;
  focusReviewValue();
}

function setReviewBusy(busy) {
  state.reviewing = busy;
  $("#close-field-review").disabled = busy;
  $("#field-review-form").setAttribute("aria-busy", String(busy));
  const isReference = !$("#field-review-links").hidden;
  for (const control of all("#field-review-form select, #field-review-form textarea, #field-review-form button, button[data-screen='adapt']")) {
    control.disabled = (busy && !control.dataset.sourcePage) || (control.id === "field-review-value" && isReference)
      || (control.id === "field-review-references" && !isReference);
  }
}

function hasVeganPlan() {
  return state.veganPlan?.protocol_version === 2 && state.veganPlan.status === "ready"
    && state.veganPlan.verification?.status === "clear";
}

function veganSelected() {
  return hasVeganPlan() && $("#vegan").getAttribute("aria-checked") === "true";
}

function currentScalingPlan(vegan) {
  return state.scalingPlan?.[vegan ? "vegan" : "original"];
}

function hasScalingPlan(vegan) {
  return ["ready", "needs_review"].includes(currentScalingPlan(vegan)?.status);
}

function renderVeganOption() {
  const control = $("#vegan");
  const available = hasVeganPlan();
  control.disabled = !available;
  if (!available) control.setAttribute("aria-checked", "false");
  const hint = available ? "The vegan plan is verified and ready to apply."
    : localizedText(state.output, "vegan:reason", state.veganPlan?.reason) || "No verified vegan plan is available for this recipe.";
  message("#vegan-plan-status", hint);
  const review = available ? [] : state.veganPlan?.review_items || [];
  const details = $("#vegan-plan-review");
  details.hidden = !review.length;
  details.innerHTML = review.map((item, index) =>
    `<li>${escape(localizedText(state.output, `vegan:review:${index}`, item.reason))}</li>`).join("");
}

async function saveFieldReview(action, clear = false) {
  if (state.reviewing) return;
  const report = state.report;
  const path = state.reviewPath;
  const referenceList = $("#field-review-references");
  const isReference = !$("#field-review-links").hidden;
  const verified = isReference ? [...referenceList.selectedOptions].map((option) => option.value)
    : clear ? null : $("#field-review-value").value.trim();
  if (action && !clear && verified === "") {
    message("#field-review-status", "Enter the verified value.", true);
    return;
  }
  const decisions = (state.review?.decisions || [])
    .filter((item) => item.path !== path)
    .map(({path, action, value}) => ({path, action, value}));
  if (action) decisions.push({path, action, value: verified});

  setReviewBusy(true);
  message("#field-review-status", !state.example
    ? "Saving review and checking adaptation plans…" : "Saving review…");
  try {
    const result = await request("/api/review", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({report: {
        recipe: report.recipe,
        extraction_issues: report.extraction_issues,
        scaling_plan: state.scalingPlan,
        vegan_plan: state.veganPlan,
        example: state.example,
      }, decisions}),
    });
    if (report !== state.report) return;
    state.review = result.review;
    state.reviewOutput = null;
    state.scalingPlan = result.scaling_plan ?? null;
    state.veganPlan = result.vegan_plan ?? null;
    state.adaptation = null;
    state.adaptationReady = false;
    state.adaptRequest++;
    clearOutput();
    updateOriginalServings();
    renderVeganOption();
    renderReview();
    renderFieldReview();
    updateReviewOutput();
    message("#field-review-status", action === "confirm" ? "Value confirmed."
      : action === "correct" ? "Correction saved." : "Review decision undone.");
  } catch (error) {
    if (report === state.report) message("#field-review-status", error.message, true);
  } finally {
    if (report === state.report) {
      setReviewBusy(false);
      if (!$("#source-dialog").open) focusReviewValue();
    }
  }
}

function quantityField(input) {
  return input.value === "" ? null : Number(input.value);
}

let adaptationTimer;
function scheduleAdaptation() {
  window.clearTimeout(adaptationTimer);
  state.adaptRequest++;
  state.adaptationReady = false;
  clearOutput();
  adaptationTimer = window.setTimeout(updateAdaptation, 220);
}

async function updateAdaptation() {
  if (state.screen !== "adapt") return;
  const sequence = ++state.adaptRequest;
  state.adaptation = null;
  state.adaptationReady = false;
  clearOutput();
  const original = $("#original-servings");
  const target = $("#target-servings");
  const servings = quantityField(target);
  const baseline = original.readOnly ? null : quantityField(original);
  const vegan = veganSelected();
  const invalid = !target.checkValidity() || (servings !== null && !original.checkValidity());
  if (servings !== null && !hasScalingPlan(vegan)) {
    renderAdaptation();
    const recovery = vegan && hasScalingPlan(false)
      ? "Clear the target servings to make the recipe vegan, or turn off Make vegan to scale the original."
      : "Clear the target servings to continue.";
    message("#adapt-status", recovery, true);
    return;
  }
  if (invalid || (servings !== null && !original.readOnly && baseline === null)) {
    renderAdaptation();
    message("#adapt-status", invalid ? "Enter positive whole numbers for servings." : "Enter the original servings to scale the recipe.", true);
    return;
  }
  if (servings === null && !vegan) {
    state.adaptationReady = true;
    message("#adapt-status");
    updateOutput();
    return;
  }
  message("#adapt-status", "Updating recipe…");
  try {
    const report = await request("/api/adapt", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        report: {
          recipe: state.report.recipe,
          ...(state.review ? {review: {decisions: state.review.decisions}} : {}),
          ...(servings !== null ? {scaling_plan: state.scalingPlan} : {}),
          ...(vegan ? {vegan_plan: state.veganPlan} : {}),
        },
        servings,
        original_servings: servings === null ? null : baseline, vegan,
      }),
    });
    if (sequence !== state.adaptRequest || state.screen !== "adapt") return;
    state.adaptation = report.adaptation;
    state.adaptationReady = true;
    message("#adapt-status");
    updateOutput();
  } catch (error) {
    if (sequence !== state.adaptRequest || state.screen !== "adapt") return;
    renderAdaptation();
    message("#adapt-status", error.message, true);
  }
}

function clearOutput() {
  state.outputRequest++;
  state.output = null;
  $("#recipe-output").hidden = true;
  $("#changes-card").hidden = true;
  $("#adaptation-review").hidden = true;
  $("#export-recipe").disabled = true;
  message("#output-status");
}

function displayLanguage() {
  return $("#output-english").checked ? "english" : "original";
}

function outputTexts(adapted = false) {
  const texts = {};
  const add = (key, text) => {
    if (typeof text === "string" && text.trim()) texts[key] = text;
  };
  (state.report.comparison?.review_items || []).forEach((item, index) => {
    add(`comparison:${index}`, item.reason);
    add(`comparison:${index}:section`, item.section);
  });
  (state.report.extraction_issues || []).forEach((item, index) => add(`extraction:${index}`, item.reason));
  if (adapted) {
    add("vegan:reason", state.veganPlan?.reason);
    (state.veganPlan?.review_items || []).forEach((item, index) => add(`vegan:review:${index}`, item.reason));
    for (const variant of ["original", "vegan"]) add(`scaling-plan:${variant}`, state.scalingPlan?.[variant]?.reason);
    (state.adaptation?.changes || []).forEach(({path, reason}) => add(`change:${path}`, reason));
    (state.adaptation?.scale?.ingredients || []).forEach(({ingredient_id, reason}) => add(`scale:${ingredient_id}`, reason));
    (state.adaptation?.review_items || []).forEach((item, index) => add(`adaptation:${index}`, item.reason));
  }
  return texts;
}

function localizeOutput(recipe, language, texts) {
  const payload = {recipe, output_language: language, texts, source_is_english: state.example && !state.review?.decisions.length};
  if (language === "original" && state.adaptation?.recipe === recipe) payload.source_recipe = currentRecipe();
  const key = JSON.stringify(payload);
  const cache = state.localizedOutputs;
  if (cache.has(key)) return cache.get(key);
  const pending = request("/api/output", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: key,
  }).then(({output}) => {
    if (output?.language !== language || !output.recipe) throw new Error("The selected output language was not returned.");
    return output;
  }).catch((error) => {
    if (cache.get(key) === pending) cache.delete(key);
    throw error;
  });
  if (cache.size >= 8) cache.delete(cache.keys().next().value);
  cache.set(key, pending);
  return pending;
}

function renderReviewOutput() {
  renderReview();
  if (!$("#field-review").open || state.reviewing) return;
  const input = $("#field-review-value");
  const references = $("#field-review-references");
  const draft = input.value;
  const selection = [input.selectionStart, input.selectionEnd];
  const linked = new Set([...references.selectedOptions].map(({value}) => value));
  renderFieldReview();
  input.value = draft;
  input.setSelectionRange(...selection);
  for (const option of references.options) option.selected = linked.has(option.value);
}

async function updateReviewOutput() {
  if (state.screen !== "review") return;
  const sequence = ++state.reviewOutputRequest;
  const recipe = currentRecipe();
  const language = displayLanguage();
  const texts = outputTexts();
  state.reviewOutput = null;
  renderReviewOutput();
  message("#output-status");
  if (language === "original" && !Object.keys(texts).length) return;
  if (language === "english" && !state.example) message("#output-status", "Preparing the English recipe. The source is shown during translation.");
  try {
    const output = await localizeOutput(recipe, language, texts);
    if (sequence !== state.reviewOutputRequest || state.screen !== "review") return;
    state.reviewOutput = output;
    renderReviewOutput();
    message("#output-status");
  } catch (error) {
    if (sequence !== state.reviewOutputRequest || state.screen !== "review") return;
    message("#output-status", `${error.message} The source is shown.`, true);
  }
}

async function updateOutput() {
  if (state.screen !== "adapt" || !state.adaptationReady) return;
  clearOutput();
  const sequence = state.outputRequest;
  const recipe = state.adaptation?.recipe ?? currentRecipe();
  const language = displayLanguage();
  const texts = outputTexts(true);
  renderAdaptation();
  renderVeganOption();
  const applyOutput = (output) => {
    state.output = output;
    renderAdaptation();
    renderVeganOption();
    $("#export-recipe").disabled = false;
  };
  if (language === "original") {
    applyOutput({language, recipe});
    if (!Object.keys(texts).length) return;
  } else if (!state.example) {
    message("#output-status", "Preparing the English recipe…");
  }
  try {
    const output = await localizeOutput(recipe, language, texts);
    if (sequence !== state.outputRequest || state.screen !== "adapt") return;
    applyOutput(output);
    message("#output-status");
  } catch (error) {
    if (sequence !== state.outputRequest || state.screen !== "adapt") return;
    const recovery = language === "english" ? "Uncheck English output to view and export the original language." : "The original recipe is available with the recorded notes.";
    message("#output-status", `${error.message} ${recovery}`, true);
  }
}

function reviewLabel(path, recipe) {
  const match = /^\/(ingredients|tools|steps)\/(\d+)/.exec(path);
  if (!match) return "Recipe";
  const item = recipe[match[1]][Number(match[2])];
  if (match[1] === "ingredients") return ingredientLabel(item);
  if (match[1] === "tools") return clean(value(item?.name)) || "Utensil";
  return stepLabel(item || {}, Number(match[2]));
}

const scalingLabels = {
  proportional: "Proportional",
  fixed: "Keep original amount",
  per_batch: "Per batch",
  review: "Review required",
};

function scalingExplanation(rule, output) {
  if (!rule) return "";
  const label = scalingLabels[rule.mode];
  const count = rule.batches ? `${rule.batches} ${rule.batches === 1 ? "batch" : "batches"}` : "";
  const perBatch = [rule.per_batch_quantity, rule.per_batch_unit].filter(Boolean).join(" ");
  const factor = rule.multiplier && rule.mode !== "fixed" ? `×${rule.multiplier}` : "";
  const check = rule.verification;
  const describeOutcome = (outcome) => {
    const amount = [outcome.quantity, outcome.unit].filter(Boolean).join(" ") || "No quantity";
    return outcome.batches > 1 ? `${amount} for ${outcome.batches} batches` : amount;
  };
  const difference = check?.materially_different
    ? `Luna ${describeOutcome(check.luna_outcome)} · Gemini ${describeOutcome(check.gemini_outcome)}` : "";
  const reason = localizedText(output, `scale:${rule.ingredient_id}`, rule.reason);
  const details = [label, count || factor, perBatch && `${perBatch} each`, difference, reason].filter(Boolean).join(" · ");
  return `<small class="change-reason">${escape(details)}</small>`;
}

function renderAdaptation() {
  const original = currentRecipe();
  const adapted = state.adaptation;
  const output = state.output;
  const title = clean(value((output?.recipe ?? adapted?.recipe ?? original).title)) || "recipe";
  $("#adapt-title").textContent = `Adapt ${title}`;
  const target = adapted?.scale?.target_servings;
  const adaptationLabel = adapted?.vegan_requested
    ? adapted.status === "needs_review" ? "Vegan proposal · Review needed" : "Vegan proposal"
    : adapted ? "Recipe adaptation" : "Reviewed recipe";
  const yieldText = target ? countLabel(target, "serving") : clean(value(output?.recipe.servings));
  $("#adapt-subtitle").textContent = [adaptationLabel, yieldText].filter(Boolean).join(" · ");
  const factor = adapted?.scale?.factor;
  const vegan = veganSelected();
  const canScale = hasScalingPlan(vegan);
  const scaleHint = !canScale
    ? localizedText(output, `scaling-plan:${vegan ? "vegan" : "original"}`, currentScalingPlan(vegan)?.reason)
      || (vegan ? "Serving adjustment is unavailable for the vegan recipe."
      : "Serving adjustment is unavailable for this recipe.")
    : factor ? `Serving scale factor: ${factor}.` : "Enter a target to scale quantities.";
  const verification = adapted?.scale?.verification ?? currentScalingPlan(vegan)?.verification;
  $("#target-hint").textContent = verification?.status === "unavailable" && canScale
    ? `${scaleHint} Gemini verification is unavailable.` : scaleHint;

  if (!output) return;
  renderRecipeOutput(output);
  $("#changes-card").hidden = !adapted;
  const changes = adapted?.changes || [];
  const changeReasons = new Map(changes.map(({path, reason}) => [path, reason]));
  const scalingRules = new Map((adapted?.scale?.ingredients || []).map((rule) => [rule.ingredient_id, rule]));
  $("#changes-title").textContent = scalingRules.size ? "Ingredient adjustments" : "Selected changes";
  $("#change-count").textContent = `${changes.length} total`;
  const rows = original.ingredients.flatMap((item, index) => {
    const changed = adapted?.recipe.ingredients[index];
    if (!changed) return [];
    const beforeName = ingredientLabel(item);
    const changedName = ingredientLabel(changed);
    const beforeAmount = ingredientAmount(item);
    const changedAmount = ingredientAmount(changed);
    const rule = scalingRules.get(item.id);
    if (beforeName === changedName && beforeAmount === changedAmount && !rule) return [];
    const localized = output.recipe.ingredients[index];
    const afterName = ingredientLabel(localized);
    const afterAmount = ingredientAmount(localized);
    const showName = beforeName !== changedName;
    const before = showName ? [beforeName, beforeAmount].filter(Boolean).join(" · ") : beforeAmount;
    const after = showName ? [afterName, afterAmount].filter(Boolean).join(" · ") : afterAmount;
    const path = `/ingredients/${index}/name`;
    const reason = showName ? localizedText(output, `change:${path}`, changeReasons.get(path)) : null;
    return `<tr><th scope="row">${escape(afterName)}</th><td>${escape(before || "—")}</td>
      <td class="changed-value">${escape(after || "—")}${reason ? `<small class="change-reason">${escape(reason)}</small>` : ""}${scalingExplanation(rule, output)}</td></tr>`;
  }).join("");
  $("#changes").innerHTML = rows ? table(["Ingredient", "Original", "Adapted"], rows)
    : '<p class="empty">No ingredient changes.</p>';
  const reviews = adapted?.review_items || [];
  const banner = $("#adaptation-review");
  banner.hidden = !reviews.length;
  banner.innerHTML = reviews.length ? `<h2>${icon("warning")} ${countLabel(reviews.length, "item")} to review</h2>
    <ul>${reviews.map((item, index) => `<li><strong>${escape(reviewLabel(item.path, output.recipe))}</strong> · ${escape(localizedText(output, `adaptation:${index}`, item.reason))}</li>`).join("")}</ul>` : "";
}

function renderRecipeOutput({language, recipe}) {
  $("#recipe-output").hidden = false;
  $("#recipe-output-language").textContent = language === "english" ? "English" : "Original language";
  const rows = recipe.ingredients.map((item) => `<tr>
    <th scope="row">${escape(ingredientLabel(item))}</th>
    <td>${escape(ingredientAmount(item) || "—")}</td>
    <td>${escape([clean(value(item.preparation)), clean(value(item.optional_when))].filter(Boolean).join(" · ") || "—")}</td>
  </tr>`).join("");
  $("#output-ingredients").innerHTML = rows ? table(["Ingredient", "Amount", "Preparation"], rows)
    : '<p class="empty">No ingredients were extracted.</p>';
  const tools = recipe.tools.map((tool) => clean(value(tool.name)) || "Needs review");
  $("#output-utensils").hidden = !tools.length;
  $("#output-tools").textContent = tools.join(", ");
  $("#output-steps").innerHTML = recipe.steps.map((step) => `<li>${escape(stepDescription(step))}</li>`).join("");
  $("#output-no-steps").hidden = Boolean(recipe.steps.length);
}

function exportReport() {
  if (!state.output) return;
  const report = {...state.report};
  if (state.review) report.review = state.review;
  if (state.scalingPlan) report.scaling_plan = state.scalingPlan;
  else delete report.scaling_plan;
  if (state.veganPlan) report.vegan_plan = state.veganPlan;
  else delete report.vegan_plan;
  if (state.adaptation) report.adaptation = state.adaptation;
  else delete report.adaptation;
  report.output = state.output;
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
  const button = event.target.closest("button");
  if (button?.dataset.screen) show(button.dataset.screen);
  if (button?.dataset.tab && button.dataset.tab !== state.tab) {
    state.tab = button.dataset.tab;
    renderReview();
  }
  const path = button ? button.dataset.editField : event.target.closest("[data-row-edit]")?.dataset.rowEdit;
  if (path) {
    const trigger = button || $(`#review-list [data-edit-field="${path}"]`);
    openFieldReview(path, trigger);
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
  const tabs = all(".tab");
  const index = tabs.findIndex((tab) => tab.dataset.tab === state.tab);
  const offset = event.key === "ArrowRight" ? 1 : -1;
  const other = tabs[(index + offset + tabs.length) % tabs.length];
  other.click();
  other.focus();
});
$("#close-field-review").addEventListener("click", () => $("#field-review").close());
$("#field-review").addEventListener("cancel", (event) => {
  if (state.reviewing) event.preventDefault();
});
$("#field-review").addEventListener("close", () => {
  const path = reviewTrigger?.dataset.editField;
  const trigger = reviewTrigger?.isConnected ? reviewTrigger
    : (path && $(`[data-edit-field="${path}"]`)) || $(`#${state.tab}-tab`);
  trigger.focus({preventScroll: true});
  reviewTrigger = null;
});
$("#field-review-path").addEventListener("change", (event) => {
  state.reviewPath = event.target.value;
  renderFieldReview();
  message("#field-review-status");
});
$("#field-review-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveFieldReview(event.submitter?.value || "correct");
});
$("#field-review-clear").addEventListener("click", () => saveFieldReview("correct", true));
$("#field-review-reopen").addEventListener("click", () => saveFieldReview(null));
$("#previous-page").addEventListener("click", () => openPage(state.page - 1));
$("#next-page").addEventListener("click", () => openPage(state.page + 1));
$("#source-dialog").addEventListener("close", () => { state.previewRequest++; });
$("#adapt-settings").addEventListener("submit", (event) => { event.preventDefault(); updateAdaptation(); });
for (const selector of ["#original-servings", "#target-servings"]) $(selector).addEventListener("input", scheduleAdaptation);
$("#output-english").addEventListener("change", () => {
  if (state.screen === "review") updateReviewOutput();
  if (state.screen === "adapt") updateOutput();
});
$("#vegan").addEventListener("click", (event) => {
  const control = event.currentTarget;
  control.setAttribute("aria-checked", String(control.getAttribute("aria-checked") !== "true"));
  scheduleAdaptation();
});
$("#export-recipe").addEventListener("click", exportReport);
