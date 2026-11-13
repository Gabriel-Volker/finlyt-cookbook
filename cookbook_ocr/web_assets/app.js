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
  review: null, reviewPath: "", reviewing: false, reviewDrafts: new Map(),
  scalingPlan: null, veganPlan: null, planDecisions: [],
  plansStatus: "current", planRequest: 0, plansError: "", savedPlans: new Map(),
  adaptation: null, adaptationReady: false, adaptRequest: 0, adaptResponses: new Map(),
  output: null, reviewOutput: null, preparedOutput: null, adaptedOutput: null,
  preparingOutput: false, preparationRequest: 0, preparation: null, translationSession: null,
  previewRequest: 0, busy: false,
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
  $("#retry-output").hidden = true;
  if (screen === "review") {
    renderReview();
    updateReviewOutput();
  }
  if (screen === "adapt") {
    if (state.plansStatus === "stale") refreshPlans();
    else updateAdaptation();
  }
  window.scrollTo(0, 0);
}

function pageCount(report) {
  const markers = Object.values(report.transcriptions || {}).flatMap(
    (text) => [...text.matchAll(/^\[page: (\d+)\]/gm)].map((match) => Number(match[1])),
  );
  return Math.max(1, ...markers);
}

async function loadReport(report, file) {
  window.clearTimeout(preparationTimer);
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
  state.preparedOutput = null;
  state.adaptedOutput = null;
  state.reviewPath = "";
  state.reviewing = false;
  state.reviewDrafts.clear();
  state.scalingPlan = report.scaling_plan ?? null;
  state.veganPlan = report.vegan_plan ?? null;
  state.planDecisions = [];
  state.plansStatus = "current";
  state.plansError = "";
  state.planRequest++;
  state.savedPlans = new Map([["[]", {vegan_plan: state.veganPlan, scaling_plan: state.scalingPlan}]]);
  state.adaptation = null;
  state.adaptationReady = false;
  state.adaptRequest++;
  state.adaptResponses.clear();
  state.preparation = null;
  state.translationSession = report.translation_session ?? [...crypto.getRandomValues(new Uint8Array(16))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  state.previewRequest++;
  state.previews.clear();

  updateOriginalServings(true);
  $("#target-servings").value = settings?.servings ?? "";
  $("#vegan").setAttribute("aria-checked", String(settings?.vegan === true && hasVeganPlan()));
  renderVeganOption();
  clearOutput();
  message("#field-review-status");
  if (file) message("#import-status", "Preparing both output languages…");
  await prepareOutput();
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
  if (!/\.(pdf|png|jpe?g|txt)$/i.test(file.name)) {
    message("#import-status", "Choose a PDF, PNG, JPEG or UTF-8 text file.", true);
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
    await loadReport(report, file);
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
  return field?.status === "needs_review" ? "Needs review" : clean(value(field)) || missing;
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
    : reviewNeeded ? "Needs review" : state.review?.decisions.length ? "Review updated"
      : report.transcriptions?.B === undefined ? "Source text extracted" : "No discrepancies detected";
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
      const optional = optionalNote(item);
      const quantity = editValue(`${base}/quantity`, `Edit quantity for ${name}`, escape(editableText(item.quantity)));
      const unit = editableText(item.unit, "");
      return `<tr data-row-edit="${base}/quantity">
        <th scope="row">${editValue(`${base}/name`, `Edit name for ${name}`, escape(name))}</th>
        <td><div class="edit-amount">${quantity}${unit ? editValue(`${base}/unit`, `Edit unit for ${name}`, escape(unit)) : ""}</div></td>
        <td>${editValue(`${base}/preparation`, `Edit preparation for ${name}`, escape(editableText(item.preparation)))}
          ${optional ? editValue(`${base}/optional_when`, `Edit optionality for ${name}`, escape(optional), "optional-note") : ""}</td>
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
      const label = tool.name.status === "inferred" ? `${name} (inferred)` : name;
      return `<tr><th scope="row">${editValue(`/tools/${index}/name`, `Edit ${name}`, escape(label))}</th></tr>`;
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
      : state.report.transcriptions?.B === undefined ? "Read the embedded or uploaded source text directly."
        : `No discrepancies were detected.${variations.length ? ` ${countLabel(variations.length, "difference")} treated as equivalent.` : ""}`;
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

function reviewInputValue() {
  return $("#field-review-links").hidden ? $("#field-review-value").value
    : [...$("#field-review-references").selectedOptions].map((option) => option.value);
}

function sameReviewValue(first, second) {
  if (Array.isArray(first) && Array.isArray(second)) {
    return first.length === second.length && first.every((value) => second.includes(value));
  }
  return String(first ?? "").trim() === String(second ?? "").trim();
}

function refreshReviewActions() {
  const path = state.reviewPath;
  const selected = reviewFields().find((item) => item.path === path);
  if (!selected) return;
  const saved = state.review?.decisions.find((item) => item.path === path);
  const current = reviewInputValue();
  const hasValue = Array.isArray(current) || current.trim() !== "";
  const draft = !sameReviewValue(current, saved ? saved.value : selected.field.value);
  if (draft) state.reviewDrafts.set(path, current);
  else state.reviewDrafts.delete(path);
  $("#field-review-confirm").disabled = state.reviewing || !hasValue
    || (selected.field.value != null && !sameReviewValue(current, selected.field.value));
  $("#field-review-save").disabled = state.reviewing || !hasValue;
  $("#field-review-discard").hidden = !draft;
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
  const editableValue = state.reviewDrafts.get(selected.path) ?? reviewedValue;
  const sourceValue = Array.isArray(editableValue) ? editableValue : [];
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
  input.value = isReference ? "" : editableValue ?? "";
  input.rows = selected.name === "instruction" ? 4 : 2;
  $("#field-review-value-label").textContent = reviewFieldLabels[selected.name];
  const needsReview = selected.field.status === "needs_review" && !decision;
  $("#field-review-clear").hidden = !reviewOptionalFields.has(selected.name) || (reviewedValue === null && !needsReview);
  const hint = $("#field-review-hint");
  const translated = state.reviewOutput?.language === "english"
    ? value(selected.path.split("/").slice(1).reduce((node, key) => node?.[key], state.reviewOutput.recipe)) : "";
  hint.textContent = selected.path === "/servings" ? "Enter the original serving count as a positive whole number."
    : selected.field.status === "inferred" && !decision ? "The utensil was inferred from the action quoted below."
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
  refreshReviewActions();
}

function focusReviewValue() {
  const input = $("#field-review-value").disabled ? $("#field-review-references") : $("#field-review-value");
  input.focus({preventScroll: true});
}

let reviewTrigger;
let returnFromReview = false;
let reviewScroll = 0;

function openFieldReview(path, trigger, returnToAdapt = false) {
  const dialog = $("#field-review");
  if (state.reviewing || dialog.open || !reviewFields().some((field) => field.path === path)) return;
  reviewTrigger = trigger;
  returnFromReview = returnToAdapt;
  reviewScroll = window.scrollY;
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
  if (!busy) refreshReviewActions();
}

function hasVeganPlan() {
  return state.plansStatus === "current" && state.veganPlan?.protocol_version === 2 && state.veganPlan.status === "ready"
    && state.veganPlan.verification?.status === "clear";
}

function veganSelected() {
  return hasVeganPlan() && $("#vegan").getAttribute("aria-checked") === "true";
}

function currentScalingPlan(vegan) {
  return state.scalingPlan?.[vegan ? "vegan" : "original"];
}

function hasScalingPlan(vegan) {
  return state.plansStatus === "current" && ["ready", "needs_review"].includes(currentScalingPlan(vegan)?.status);
}

function reviewIssuePath(path) {
  if (typeof path !== "string") return null;
  const match = /^\/(ingredients|tools|steps)\/(\d+)$/.exec(path);
  if (match) {
    const [, collection, index] = match;
    const fields = collection === "steps" ? ["instruction", "condition", "duration"]
      : collection === "ingredients" ? ["name", "quantity", "unit"] : ["name"];
    const record = currentRecipe()[collection]?.[Number(index)];
    path += `/${fields.find((name) => record?.[name]?.status === "needs_review") || fields[0]}`;
  }
  return reviewFields().some((field) => field.path === path) ? path : null;
}

function reviewIssueLink(path, recipe) {
  const editable = reviewIssuePath(path);
  if (!editable) return "";
  const field = editable.split("/").pop();
  const label = editable === "/servings" ? "Original servings"
    : `${reviewLabel(editable, recipe)} · ${reviewFieldLabels[field] || "Review"}`;
  return ` <button type="button" class="button text issue-link" data-review-issue="${escape(editable)}">${escape(label)}</button>`;
}

function renderVeganOption() {
  const control = $("#vegan");
  const available = hasVeganPlan();
  control.disabled = !available;
  const hint = available ? "The vegan plan is verified and ready to apply."
    : state.plansStatus !== "current" ? "A current vegan plan is needed for the reviewed recipe."
      : localizedText(state.output, "vegan:reason", state.veganPlan?.reason) || "No verified vegan plan is available for this recipe.";
  message("#vegan-plan-status", hint);
  const review = available || state.plansStatus !== "current" ? [] : state.veganPlan?.review_items || [];
  const details = $("#vegan-plan-review");
  details.hidden = !review.length;
  const recipe = state.output?.recipe ?? currentRecipe();
  details.innerHTML = review.map((item, index) =>
    `<li>${escape(localizedText(state.output, `vegan:review:${index}`, item.reason))}${reviewIssueLink(item.path, recipe)}</li>`).join("");
  $("#vegan-review-source").hidden = available || state.plansStatus !== "current" || Boolean(review.length)
    || !currentRecipeHasPendingFields();
}

function currentRecipeHasPendingFields() {
  return state.review ? Boolean(state.review.unresolved_paths?.length) : Boolean(flaggedFields().length);
}

function reviewDecisions() {
  return (state.review?.decisions || []).map(({path, action, value}) => ({path, action, value}));
}

function reviewPlanKey() {
  return JSON.stringify(reviewDecisions().sort((first, second) => first.path.localeCompare(second.path)));
}

function recipeRequest(decisions = reviewDecisions()) {
  return {
    report: {
      recipe: state.report.recipe,
      extraction_issues: state.report.extraction_issues,
      scaling_plan: state.scalingPlan,
      vegan_plan: state.veganPlan,
      plan_decisions: state.planDecisions,
      example: state.example,
    },
    decisions,
  };
}

function applyPlans(plans) {
  state.scalingPlan = plans.scaling_plan ?? null;
  state.veganPlan = plans.vegan_plan ?? null;
  state.planDecisions = reviewDecisions();
  state.plansStatus = "current";
  state.plansError = "";
  state.adaptResponses.clear();
}

async function refreshPlans(retry = false) {
  if (state.plansStatus === "pending") return;
  window.clearTimeout(preparationTimer);
  const sequence = ++state.planRequest;
  const key = reviewPlanKey();
  state.plansStatus = "pending";
  state.plansError = "";
  state.adaptResponses.clear();
  if (state.screen === "adapt") updateAdaptation();
  try {
    const result = await request("/api/plans", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...recipeRequest(), retry}),
    });
    if (sequence !== state.planRequest) return;
    applyPlans(result);
    if (state.savedPlans.size >= 8) state.savedPlans.delete(state.savedPlans.keys().next().value);
    state.savedPlans.set(key, result);
    await prepareOutput({refreshAdaptation: false});
    if (sequence !== state.planRequest) return;
  } catch (error) {
    if (sequence !== state.planRequest) return;
    state.plansStatus = "failed";
    state.plansError = error.message;
    await prepareOutput({refreshAdaptation: false});
    if (sequence !== state.planRequest) return;
  }
  if (state.screen === "adapt") updateAdaptation();
}

async function saveFieldReview(action, clear = false) {
  if (state.reviewing) return;
  const report = state.report;
  const path = state.reviewPath;
  const input = reviewInputValue();
  const verified = Array.isArray(input) ? input : clear ? null : input.trim();
  if (action && !clear && verified === "") {
    message("#field-review-status", "Enter the verified value.", true);
    return;
  }
  const decisions = reviewDecisions().filter((item) => item.path !== path);
  if (action) decisions.push({path, action, value: verified});

  setReviewBusy(true);
  message("#field-review-status", "Saving review…");
  try {
    const result = await request("/api/review", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...recipeRequest(decisions), refresh_plans: false}),
    });
    if (report !== state.report) return;
    state.review = result.review;
    state.reviewDrafts.delete(path);
    state.reviewOutput = null;
    state.preparedOutput = null;
    state.adaptedOutput = null;
    state.planRequest++;
    const saved = state.savedPlans.get(reviewPlanKey());
    if (saved) applyPlans(saved);
    else {
      state.plansStatus = "stale";
      state.plansError = "";
    }
    state.adaptation = null;
    state.adaptationReady = false;
    state.adaptRequest++;
    state.adaptResponses.clear();
    clearOutput();
    updateOriginalServings();
    renderVeganOption();
    renderReview();
    renderFieldReview();
    schedulePreparation();
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
  $("#export-recipe").disabled = true;
  message("#adapt-status", "Updating preview…");
  adaptationTimer = window.setTimeout(updateAdaptation, 60);
}

async function updateAdaptation() {
  window.clearTimeout(adaptationTimer);
  if (state.screen !== "adapt") return;
  const sequence = ++state.adaptRequest;
  state.adaptationReady = false;
  $("#export-recipe").disabled = true;
  renderVeganOption();
  const original = $("#original-servings");
  const target = $("#target-servings");
  const servings = quantityField(target);
  const baseline = original.readOnly ? null : quantityField(original);
  const vegan = veganSelected();
  const invalid = !target.checkValidity() || (servings !== null && !original.checkValidity());
  let scaleMessage = "";
  if (invalid) scaleMessage = "Enter positive whole numbers for servings.";
  else if (servings !== null && !original.readOnly && baseline === null) {
    scaleMessage = "Enter the original servings to scale the recipe.";
  } else if (servings !== null && !hasScalingPlan(vegan) && (baseline ?? Number(original.value)) !== servings) {
    scaleMessage = state.plansStatus === "pending" ? "The scaling plan is still updating."
      : vegan ? "Serving adjustment is unavailable for the vegan recipe."
      : "Serving adjustment is unavailable for this recipe.";
  }
  const requestedServings = scaleMessage ? null : servings;
  if (requestedServings === null && !vegan) {
    state.adaptation = null;
    state.adaptedOutput = null;
    state.adaptationReady = true;
    message("#adapt-status", scaleMessage ? `${scaleMessage} Showing the reviewed recipe.` : "", Boolean(scaleMessage));
    updateOutput();
    return;
  }
  message("#adapt-status", "Updating recipe…");
  try {
    const adapt = (requested) => {
      const responses = state.adaptResponses;
      const key = JSON.stringify([requested, requested === null ? null : baseline, vegan]);
      if (responses.has(key)) return responses.get(key);
      const payload = {
        report: {
          recipe: state.report.recipe,
          ...(state.review ? {review: {decisions: state.review.decisions}} : {}),
          ...(requested !== null ? {scaling_plan: state.scalingPlan} : {}),
          ...(vegan ? {vegan_plan: state.veganPlan} : {}),
        },
        servings: requested, original_servings: requested === null ? null : baseline, vegan,
        translation_session: state.translationSession, source_is_english: sourceIsEnglish(),
        texts: outputTexts(),
      };
      const pending = request("/api/adapt", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
      }).then(({adaptation, outputs, output_errors}) => ({adaptation, outputs, output_errors}))
        .catch((error) => {
          if (responses.get(key) === pending) responses.delete(key);
          throw error;
        });
      if (responses.size === 3) responses.delete(responses.keys().next().value);
      responses.set(key, pending);
      return pending;
    };
    let report;
    try {
      report = await adapt(requestedServings);
    } catch (error) {
      if (sequence !== state.adaptRequest || state.screen !== "adapt") return;
      if (!vegan || requestedServings === null) throw error;
      scaleMessage = error.message;
      report = await adapt(null);
    }
    if (sequence !== state.adaptRequest || state.screen !== "adapt") return;
    state.adaptation = report.adaptation;
    state.adaptedOutput = unpackOutputs(report, report.adaptation.recipe, outputTexts());
    state.adaptationReady = true;
    message("#adapt-status", scaleMessage ? `${scaleMessage} Showing the vegan recipe at its original yield.` : "", Boolean(scaleMessage));
    updateOutput();
  } catch (error) {
    if (sequence !== state.adaptRequest || state.screen !== "adapt") return;
    state.adaptation = null;
    state.adaptedOutput = null;
    state.adaptationReady = true;
    message("#adapt-status", `${error.message} Showing the reviewed recipe.`, true);
    updateOutput();
  }
}

function clearOutput() {
  state.output = null;
  $("#recipe-output").hidden = true;
  $("#changes-card").hidden = true;
  $("#adaptation-review").hidden = true;
  $("#export-recipe").disabled = true;
  message("#output-status");
  $("#retry-output").hidden = true;
}

function displayLanguage() {
  return $("#output-english").checked ? "english" : "original";
}

function outputTexts() {
  const texts = {};
  const add = (key, text) => {
    if (typeof text === "string" && text.trim()) texts[key] = text;
  };
  (state.report.comparison?.review_items || []).forEach((item, index) => {
    add(`comparison:${index}`, item.reason);
    add(`comparison:${index}:section`, item.section);
  });
  (state.report.extraction_issues || []).forEach((item, index) => add(`extraction:${index}`, item.reason));
  if (state.plansStatus === "current") {
    add("vegan:reason", state.veganPlan?.reason);
    (state.veganPlan?.review_items || []).forEach((item, index) => add(`vegan:review:${index}`, item.reason));
    for (const variant of ["original", "vegan"]) {
      const plan = state.scalingPlan?.[variant];
      add(`scaling-plan:${variant}`, plan?.reason);
      (plan?.review_items || []).forEach((item, index) => add(`scaling-plan:${variant}:${index}`, item.reason));
    }
  }
  return texts;
}

function sourceIsEnglish() {
  return state.example && !state.review?.decisions.length;
}

function unpackOutputs(result, recipe, texts) {
  const outputs = result.outputs || {};
  return {
    original: outputs.original || {language: "original", recipe, texts},
    english: outputs.english || null,
    errors: result.output_errors || {},
  };
}

let preparationTimer;
function schedulePreparation() {
  window.clearTimeout(preparationTimer);
  state.preparationRequest++;
  state.preparingOutput = true;
  updateReviewOutput();
  preparationTimer = window.setTimeout(prepareOutput, 400);
}

async function prepareOutput({refreshAdaptation = true, retry = false} = {}) {
  window.clearTimeout(preparationTimer);
  const sequence = ++state.preparationRequest;
  const recipe = currentRecipe();
  const texts = outputTexts();
  if (sourceIsEnglish()) {
    state.preparingOutput = false;
    state.preparedOutput = unpackOutputs({outputs: {english: {language: "english", recipe, texts}}}, recipe, texts);
    if (state.screen === "review") updateReviewOutput();
    return;
  }
  state.preparingOutput = true;
  if (state.screen === "review") updateReviewOutput();
  if (state.screen === "adapt") updateOutput();
  const planReasons = [
    ...(state.veganPlan?.changes || []),
    ...(state.scalingPlan?.original?.rules || []),
    ...(state.scalingPlan?.vegan?.rules || []),
    ...(state.scalingPlan?.original?.verification?.rules || []),
    ...(state.scalingPlan?.vegan?.verification?.rules || []),
  ].map(({reason}) => reason).filter((reason) => typeof reason === "string" && reason.trim());
  const payload = {
    recipe, texts,
    translation_session: state.translationSession,
    can_scale: hasScalingPlan(false) || hasScalingPlan(true),
    prefetch_texts: state.plansStatus === "current" ? [...new Set(planReasons)] : [],
  };
  if (hasVeganPlan()) payload.vegan_plan = state.veganPlan;
  const key = JSON.stringify(payload);
  let preparation = state.preparation;
  if (preparation?.key !== key || (retry && !preparation.pending)) {
    preparation = {key, pending: true};
    preparation.result = request("/api/prepare-output", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: key,
    }).catch((error) => ({output_errors: {english: error.message, original: error.message}}))
      .finally(() => { preparation.pending = false; });
    state.preparation = preparation;
  }
  const result = await preparation.result;
  if (sequence !== state.preparationRequest) return;
  state.preparedOutput = unpackOutputs(result, recipe, texts);
  state.preparingOutput = false;
  state.adaptResponses.clear();
  if (state.screen === "review") updateReviewOutput();
  if (refreshAdaptation && state.screen === "adapt") {
    if (!state.adaptationReady || state.adaptation) updateAdaptation();
    else updateOutput();
  }
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

function renderOutputStatus(outputs, language) {
  const error = outputs?.errors?.[language];
  if (state.preparingOutput && (!outputs || !outputs[language])) {
    message("#output-status", "Preparing both output languages. The source is shown during translation.");
    $("#retry-output").hidden = true;
    return;
  }
  const englishMissing = language === "english" && !outputs?.english;
  const recovery = englishMissing ? "Showing the original recipe text. Uncheck English output to export it."
    : error && language === "original" ? "The original recipe is available with the recorded notes." : "";
  message("#output-status", [error, recovery].filter(Boolean).join(" "), Boolean(error || englishMissing));
  $("#retry-output").hidden = !error && !englishMissing;
}

function updateReviewOutput() {
  if (state.screen !== "review") return;
  const language = displayLanguage();
  const outputs = state.preparedOutput;
  state.reviewOutput = outputs?.[language] ?? outputs?.original ?? null;
  renderReviewOutput();
  renderOutputStatus(outputs, language);
}

function updateOutput() {
  if (state.screen !== "adapt" || !state.adaptationReady) return;
  const recipe = state.adaptation?.recipe ?? currentRecipe();
  const language = displayLanguage();
  const outputs = state.adaptation ? state.adaptedOutput : state.preparedOutput;
  const fallback = {language: "original", recipe, texts: outputTexts()};
  state.output = outputs?.[language] ?? outputs?.original ?? fallback;
  renderAdaptation();
  renderVeganOption();
  $("#export-recipe").disabled = state.output.language !== language;
  renderOutputStatus(outputs, language);
}

function reviewLabel(path, recipe) {
  const match = /^\/(ingredients|tools|steps)\/(\d+)/.exec(path);
  if (!match) return "Recipe";
  const index = Number(match[2]);
  const item = recipe[match[1]]?.[index];
  if (match[1] === "ingredients" && !value(item?.name)) return `Ingredient ${index + 1}`;
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
  const variant = vegan ? "vegan" : "original";
  const canScale = hasScalingPlan(vegan);
  const plan = currentScalingPlan(vegan);
  const scaleHint = state.plansStatus !== "current" ? "A current scaling plan is needed for the reviewed recipe."
    : !canScale
    ? localizedText(output, `scaling-plan:${variant}`, plan?.reason)
      || (vegan ? "Serving adjustment is unavailable for the vegan recipe."
      : "Serving adjustment is unavailable for this recipe.")
    : factor ? `Serving scale factor: ${factor}.` : "Enter a target to scale quantities.";
  const verification = adapted?.scale?.verification ?? plan?.verification;
  const partialScale = plan?.recovered_ingredients?.length || plan?.verification?.recovered_ingredients?.length;
  const verificationHint = verification?.status === "unavailable" ? "Gemini verification is unavailable."
    : partialScale ? "Some quantities require review before scaling." : "";
  $("#target-hint").textContent = [scaleHint, canScale && verificationHint].filter(Boolean).join(" ");

  const planIssues = state.plansStatus === "current" ? plan?.review_items || [] : [];
  const scaleIssues = $("#scaling-plan-review");
  scaleIssues.hidden = !planIssues.length;
  scaleIssues.innerHTML = planIssues.map((item, index) => `<li>${escape(localizedText(output,
    `scaling-plan:${variant}:${index}`, item.reason))}${reviewIssueLink(item.path, original)}</li>`).join("");
  const refresh = $("#retry-plans");
  const retryable = ["invalid_output", "provider_unavailable", "verification_unavailable"];
  const retryAvailable = [state.veganPlan, state.scalingPlan?.original, state.scalingPlan?.vegan].some((candidate) =>
    retryable.includes(candidate?.reason_code) || retryable.includes(candidate?.verification?.reason_code)
    || candidate?.recovered_ingredients?.length || candidate?.verification?.recovered_ingredients?.length);
  refresh.hidden = state.plansStatus === "current" && !retryAvailable;
  refresh.disabled = state.plansStatus === "pending";
  refresh.textContent = state.plansStatus === "pending" ? "Updating adaptation plans…"
    : state.plansStatus === "stale" ? "Update adaptation plans" : "Retry adaptation plans";
  message("#plans-status", state.plansStatus === "failed" ? state.plansError
    : state.plansStatus === "pending" ? "You can review or export the recipe while the plans update." : "",
    state.plansStatus === "failed");

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
    <ul>${reviews.map((item, index) => `<li><strong>${escape(reviewLabel(item.path, output.recipe))}</strong> · ${escape(localizedText(output, `adaptation:${index}`, item.reason))}${reviewIssueLink(item.path, original)}</li>`).join("")}</ul>` : "";
}

function renderRecipeOutput({language, recipe}) {
  $("#recipe-output").hidden = false;
  $("#recipe-output-title").textContent = state.adaptation?.vegan_requested ? "Vegan recipe" : state.adaptation?.scale ? "Scaled recipe" : "Reviewed recipe";
  $("#recipe-output-language").textContent = language === "english" ? "English" : "Original language";
  const rows = recipe.ingredients.map((item) => `<tr>
    <th scope="row">${escape(ingredientLabel(item))}</th>
    <td>${escape(ingredientAmount(item) || "—")}</td>
    <td>${escape(editableText(item.preparation))}${optionalNote(item) ? `<small class="optional-note">${escape(optionalNote(item))}</small>` : ""}</td>
  </tr>`).join("");
  $("#output-ingredients").innerHTML = rows ? table(["Ingredient", "Amount", "Preparation"], rows)
    : '<p class="empty">No ingredients were extracted.</p>';
  const tools = recipe.tools.map((tool) => {
    const name = clean(value(tool.name)) || "Needs review";
    return tool.name.status === "inferred" ? `${name} (inferred)` : name;
  });
  $("#output-utensils").hidden = !tools.length;
  $("#output-tools").textContent = tools.join(", ");
  $("#output-steps").innerHTML = recipe.steps.map((step) => `<li>${escape(stepDescription(step))}</li>`).join("");
  $("#output-no-steps").hidden = Boolean(recipe.steps.length);
}

function optionalNote(item) {
  if (item.optional_when?.status === "needs_review") return "Check optionality";
  const condition = clean(value(item.optional_when));
  if (!condition) return "";
  return /^(?:optional|opcional|you can also|auch kann man|também pode)[.!:]?$/iu.test(condition)
    ? "Optional" : `Optional · ${condition}`;
}

function exportReport() {
  if (!state.output || $("#export-recipe").disabled) return;
  const report = {...state.report};
  if (state.review) report.review = state.review;
  if (state.scalingPlan && state.plansStatus === "current") report.scaling_plan = state.scalingPlan;
  else delete report.scaling_plan;
  if (state.veganPlan && state.plansStatus === "current") report.vegan_plan = state.veganPlan;
  else delete report.vegan_plan;
  if (state.plansStatus === "current") report.plan_decisions = state.planDecisions;
  else delete report.plan_decisions;
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
    if (typeof result.text === "string") {
      const preview = document.createElement("pre");
      preview.className = "preview-text";
      preview.textContent = result.text;
      content.replaceChildren(preview);
      return;
    }
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
  await loadReport(await request("/assets/example.json"), null);
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
  const issue = button?.dataset.reviewIssue;
  if (issue) {
    const collection = issue.split("/")[1];
    if (collection in reviewTabs) state.tab = collection;
    show("review");
    openFieldReview(issue, button, true);
    return;
  }
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
  if (returnFromReview) {
    returnFromReview = false;
    reviewTrigger = null;
    show("adapt");
    $("#settings-title").focus({preventScroll: true});
    return;
  }
  const path = reviewTrigger?.dataset.editField;
  const trigger = reviewTrigger?.isConnected ? reviewTrigger
    : (path && $(`[data-edit-field="${path}"]`)) || $(`#${state.tab}-tab`);
  trigger?.focus({preventScroll: true});
  window.scrollTo(0, reviewScroll);
  reviewTrigger = null;
});
$("#field-review-path").addEventListener("change", (event) => {
  refreshReviewActions();
  state.reviewPath = event.target.value;
  renderFieldReview();
  message("#field-review-status");
  focusReviewValue();
});
for (const selector of ["#field-review-value", "#field-review-references"]) {
  $(selector).addEventListener("input", refreshReviewActions);
}
$("#field-review-discard").addEventListener("click", () => {
  state.reviewDrafts.delete(state.reviewPath);
  renderFieldReview();
  message("#field-review-status");
  focusReviewValue();
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
$("#retry-output").addEventListener("click", async () => {
  $("#retry-output").hidden = true;
  message("#output-status", "Preparing both output languages…");
  await prepareOutput({retry: true});
});
$("#vegan").addEventListener("click", (event) => {
  const control = event.currentTarget;
  control.setAttribute("aria-checked", String(control.getAttribute("aria-checked") !== "true"));
  updateAdaptation();
});
$("#retry-plans").addEventListener("click", () => refreshPlans(true));
$("#export-recipe").addEventListener("click", exportReport);
