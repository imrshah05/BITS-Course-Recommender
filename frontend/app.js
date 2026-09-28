const statusElement = document.querySelector("#api-status");
const recommendationForm = document.querySelector("#recommendation-form");
const formSummary = document.querySelector("#form-summary");
const recommendationStatus = document.querySelector("#recommendation-status");
const recommendButton = document.querySelector("#recommend-button");
const resultsPanel = document.querySelector("#recommendation-results");
const resultsSummary = document.querySelector("#results-summary");
const resultNotice = document.querySelector("#result-notice");
const recommendedResults = document.querySelector("#recommended-results");
const relatedResults = document.querySelector("#related-results");
const recommendedCount = document.querySelector("#recommended-count");
const relatedCount = document.querySelector("#related-count");
const RELATED_DISPLAY_LIMIT = 8;
const SEARCH_DISPLAY_LIMIT = 8;

const dashboardState = {
  programmes: new Set(),
  courses: [],
  completed: new Map(),
  ongoing: new Map(),
  suggested: new Map(),
  dismissedSuggestions: new Set(),
  suggestionRequestId: 0,
};

function setServiceState(label, state) {
  statusElement.textContent = label;
  statusElement.className = `status ${state || ""}`.trim();
}

async function loadDashboardOptions() {
  try {
    const response = await fetch("/api/options", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Options request failed: ${response.status}`);
    const payload = await response.json();
    const options = payload.options;
    if (!options || !Array.isArray(options.programmes) || !Array.isArray(options.courses)) {
      throw new Error("The academic options response is invalid.");
    }
    dashboardState.programmes = new Set(options.programmes);
    dashboardState.courses = options.courses;
    populateOptions(options);
    configureProgrammePicker("programme");
    configureProgrammePicker("second-programme");
    configureCoursePicker("completed");
    configureCoursePicker("ongoing");
    recommendButton.disabled = false;
    recommendationStatus.textContent = "Ready when you are.";
    setServiceState("Planner ready", "connected");
  } catch (error) {
    setServiceState("Planner unavailable", "unavailable");
    recommendationStatus.textContent = "Academic options could not be loaded. Refresh after starting the service.";
    showIssues([{ message: error.message }], "We could not prepare the planner:");
  }
}

function populateOptions(options) {
  const yearSelect = document.querySelector("#academic-year");
  options.academic_years.forEach((year) => {
    const option = document.createElement("option");
    option.value = String(year);
    option.textContent = `Year ${year}`;
    yearSelect.append(option);
  });
  const semesterSelect = document.querySelector("#semester");
  options.semesters.forEach((semester) => {
    const option = document.createElement("option");
    option.value = String(semester);
    option.textContent = `Semester ${semester}`;
    semesterSelect.append(option);
  });
}

function normalizeProgrammeSearch(value) {
  return String(value || "").toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
}

function configureProgrammePicker(inputId) {
  const input = document.querySelector(`#${inputId}`);
  const suggestions = document.querySelector(`#${inputId}-suggestions`);
  const render = () => {
    const query = normalizeProgrammeSearch(input.value);
    suggestions.replaceChildren();
    if (!query) {
      suggestions.hidden = true;
      return;
    }
    const matches = [...dashboardState.programmes].filter((programme) =>
      normalizeProgrammeSearch(programme).includes(query)).slice(0, SEARCH_DISPLAY_LIMIT);
    if (!matches.length) {
      const empty = document.createElement("p");
      empty.className = "suggestion-empty";
      empty.textContent = "No matching processed programme found.";
      suggestions.append(empty);
    }
    matches.forEach((programme) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suggestion-option";
      button.setAttribute("role", "option");
      button.textContent = programme;
      button.addEventListener("click", () => {
        input.value = programme;
        suggestions.hidden = true;
        refreshCourseSuggestions();
      });
      suggestions.append(button);
    });
    suggestions.hidden = false;
  };
  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("change", refreshCourseSuggestions);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") suggestions.hidden = true;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(`[data-programme-picker="${inputId}"]`)) {
      suggestions.hidden = true;
    }
  });
}

function configureCoursePicker(kind) {
  const search = document.querySelector(`#${kind}-course-search`);
  const suggestions = document.querySelector(`#${kind}-suggestions`);
  search.addEventListener("input", () => renderSuggestions(kind));
  search.addEventListener("focus", () => renderSuggestions(kind));
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") suggestions.hidden = true;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(`[data-picker="${kind}"]`)) suggestions.hidden = true;
  });
}

function renderSuggestions(kind) {
  const search = document.querySelector(`#${kind}-course-search`);
  const suggestions = document.querySelector(`#${kind}-suggestions`);
  const query = search.value.trim().toLocaleLowerCase();
  suggestions.replaceChildren();
  if (!query) {
    suggestions.hidden = true;
    return;
  }
  const current = dashboardState[kind];
  const opposite = dashboardState[kind === "completed" ? "ongoing" : "completed"];
  const matches = dashboardState.courses.filter((course) =>
    course.label.toLocaleLowerCase().includes(query) && !current.has(course.course_code)
  ).slice(0, SEARCH_DISPLAY_LIMIT);
  if (!matches.length) {
    const empty = document.createElement("p");
    empty.className = "suggestion-empty";
    empty.textContent = "No matching course found.";
    suggestions.append(empty);
  }
  matches.forEach((course) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-option";
    button.setAttribute("role", "option");
    button.disabled = opposite.has(course.course_code);
    button.textContent = opposite.has(course.course_code)
      ? `${course.label} · already ${kind === "completed" ? "ongoing" : "completed"}`
      : course.label;
    button.addEventListener("click", () => addCourse(kind, course));
    suggestions.append(button);
  });
  suggestions.hidden = false;
}

function addCourse(kind, course) {
  const opposite = dashboardState[kind === "completed" ? "ongoing" : "completed"];
  if (opposite.has(course.course_code)) {
    showIssues([{ message: `${course.course_code} is already selected as ${kind === "completed" ? "ongoing" : "completed"}.` }]);
    return;
  }
  dashboardState[kind].set(course.course_code, course);
  document.querySelector(`#${kind}-course-search`).value = "";
  document.querySelector(`#${kind}-suggestions`).hidden = true;
  renderSelectedCourses(kind);
}

function removeCourse(kind, code) {
  dashboardState[kind].delete(code);
  renderSelectedCourses(kind);
  if (kind === "completed") renderCourseSuggestions();
}

function renderSelectedCourses(kind) {
  const container = document.querySelector(`#${kind}-selected`);
  const count = document.querySelector(`#${kind}-count`);
  const courses = [...dashboardState[kind].values()];
  container.replaceChildren();
  count.textContent = `${courses.length} selected`;
  courses.forEach((course) => {
    const chip = document.createElement("span");
    chip.className = "course-chip";
    chip.title = course.course_title || course.course_code;
    const label = document.createElement("span");
    label.textContent = course.course_code;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-remove";
    remove.setAttribute("aria-label", `Remove ${course.course_code} from ${kind} courses`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeCourse(kind, course.course_code));
    chip.append(label, remove);
    container.append(chip);
  });
}

function suggestionProfile() {
  const primary = document.querySelector("#programme").value.trim();
  const second = document.querySelector("#second-programme").value.trim();
  const year = Number(document.querySelector("#academic-year").value);
  const semester = Number(document.querySelector("#semester").value);
  const programmes = [primary, second].filter((value) => dashboardState.programmes.has(value));
  return { programmes, current_academic_year: year, current_semester: semester };
}

async function refreshCourseSuggestions() {
  const profile = suggestionProfile();
  const status = document.querySelector("#suggestion-status");
  const list = document.querySelector("#suggested-course-list");
  const confirmAll = document.querySelector("#confirm-all-suggestions");
  const requestId = ++dashboardState.suggestionRequestId;
  dashboardState.suggested.clear();
  dashboardState.dismissedSuggestions.clear();
  list.replaceChildren();
  confirmAll.hidden = true;
  if (!profile.programmes.length || !profile.current_academic_year || !profile.current_semester) {
    status.textContent = "Select a programme, year, and semester to check for source-backed suggestions.";
    return;
  }
  status.textContent = "Checking processed programme charts…";
  try {
    const response = await fetch("/api/course-suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(profile),
    });
    const payload = await response.json();
    if (requestId !== dashboardState.suggestionRequestId) return;
    if (!response.ok || !payload.course_suggestions) {
      throw new Error(payload.message || "Course suggestions could not be loaded.");
    }
    const result = payload.course_suggestions;
    (result.suggestions || []).forEach((item) =>
      dashboardState.suggested.set(item.course_code, item));
    renderCourseSuggestions();
    if (result.suggestions?.length) {
      status.textContent = `${result.suggestions.length} source-backed courses are normally scheduled before this semester. Confirm only the ones you completed.`;
      confirmAll.hidden = false;
    } else if (result.limitations?.length) {
      status.textContent = result.limitations.map((item) => item.message).join(" ");
    } else {
      status.textContent = "No source-backed required courses are scheduled before this semester for the selected programme.";
    }
  } catch (error) {
    if (requestId !== dashboardState.suggestionRequestId) return;
    status.textContent = `Suggestions unavailable: ${error.message}`;
  }
}

function renderCourseSuggestions() {
  const list = document.querySelector("#suggested-course-list");
  list.replaceChildren();
  [...dashboardState.suggested.values()].filter((item) =>
    !dashboardState.dismissedSuggestions.has(item.course_code) &&
    !dashboardState.completed.has(item.course_code)
  ).forEach((item) => {
    const row = document.createElement("div");
    row.className = "suggested-course";
    const identity = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = [item.course_code, item.course_title].filter(Boolean).join(" — ");
    const detail = document.createElement("span");
    detail.textContent = `Expected in year ${item.expected_year}, semester ${item.expected_semester} · ${item.programme_scopes.join(", ")}`;
    identity.append(title, detail);
    const actions = document.createElement("div");
    actions.className = "suggestion-actions";
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.textContent = "Confirm completed";
    confirm.addEventListener("click", () => confirmSuggestion(item));
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "dismiss-suggestion";
    dismiss.textContent = "Remove";
    dismiss.addEventListener("click", () => {
      dashboardState.dismissedSuggestions.add(item.course_code);
      renderCourseSuggestions();
    });
    actions.append(confirm, dismiss);
    row.append(identity, actions);
    list.append(row);
  });
}

function confirmSuggestion(item) {
  if (dashboardState.ongoing.has(item.course_code)) {
    showIssues([{ message: `${item.course_code} is selected as ongoing. Remove it there before confirming completion.` }]);
    return;
  }
  dashboardState.completed.set(item.course_code, {
    course_code: item.course_code,
    course_title: item.course_title,
    label: [item.course_code, item.course_title].filter(Boolean).join(" — "),
  });
  renderSelectedCourses("completed");
  renderCourseSuggestions();
}

function confirmAllSuggestions() {
  [...dashboardState.suggested.values()].forEach((item) => {
    if (!dashboardState.dismissedSuggestions.has(item.course_code) &&
        !dashboardState.ongoing.has(item.course_code)) {
      dashboardState.completed.set(item.course_code, {
        course_code: item.course_code,
        course_title: item.course_title,
        label: [item.course_code, item.course_title].filter(Boolean).join(" — "),
      });
    }
  });
  renderSelectedCourses("completed");
  renderCourseSuggestions();
}

document.querySelector("#academic-year").addEventListener("change", refreshCourseSuggestions);
document.querySelector("#semester").addEventListener("change", refreshCourseSuggestions);
document.querySelector("#confirm-all-suggestions").addEventListener("click", confirmAllSuggestions);

function courseEntries(kind) {
  return [...dashboardState[kind].keys()].map((courseCode) => ({ course_code: courseCode }));
}

function profilePayload() {
  return {
    programme: document.querySelector("#programme").value.trim(),
    second_programme: document.querySelector("#second-programme").value.trim() || null,
    current_academic_year: Number(document.querySelector("#academic-year").value),
    current_semester: Number(document.querySelector("#semester").value),
    completed_courses: courseEntries("completed"),
    ongoing_courses: courseEntries("ongoing"),
  };
}

function localValidationIssues() {
  const issues = [];
  const primary = document.querySelector("#programme").value.trim();
  const second = document.querySelector("#second-programme").value.trim();
  if (primary && !dashboardState.programmes.has(primary)) {
    issues.push({ message: "Choose the primary programme from the available programme list." });
  }
  if (second && !dashboardState.programmes.has(second)) {
    issues.push({ message: "Choose the second programme from the available programme list." });
  }
  if (primary && second && primary === second) {
    issues.push({ message: "Primary and second programme must be different." });
  }
  const overlap = [...dashboardState.completed.keys()].filter((code) => dashboardState.ongoing.has(code));
  if (overlap.length) issues.push({ message: `${overlap.join(", ")} cannot be both completed and ongoing.` });
  return issues;
}

function showIssues(issues, headingText = "Please review these details:") {
  formSummary.replaceChildren();
  if (!issues.length) {
    formSummary.hidden = true;
    return;
  }
  const heading = document.createElement("strong");
  heading.textContent = headingText;
  const list = document.createElement("ul");
  issues.forEach((issue) => {
    const item = document.createElement("li");
    item.textContent = issue.message || "A value needs your attention.";
    list.append(item);
  });
  formSummary.append(heading, list);
  formSummary.hidden = false;
  formSummary.focus();
}

function reasonLabel(reason) {
  return String(reason || "verification required").replaceAll("_", " ");
}

function eligibilityVerified(item) {
  return item.academic_confidence?.state === "eligibility_verified";
}

function confidenceBadge(item) {
  const verified = eligibilityVerified(item);
  const badge = document.createElement("span");
  badge.className = `course-status ${verified ? "verified" : "unverified"}`;
  badge.textContent = item.academic_confidence?.label
    || (verified ? "Eligibility verified" : "Eligibility verification required");
  return badge;
}

function resultCard(item, weakMatch = false) {
  const card = document.createElement("article");
  card.className = `course-card ${weakMatch ? "weak-match" : "strong-match"}`
    + (eligibilityVerified(item) ? " eligibility-verified" : " eligibility-unverified");
  const top = document.createElement("div");
  top.className = "course-card-top";
  const heading = document.createElement("h4");
  heading.textContent = [item.course_code, item.course_title].filter(Boolean).join(" — ") || "Course identity unavailable";
  top.append(heading, confidenceBadge(item));
  const explanation = document.createElement("p");
  explanation.className = "course-explanation";
  explanation.textContent = item.explanation?.text || "The available structured evidence does not include an explanation.";
  card.append(top, explanation);

  const details = [];
  if (item.eligibility_state) details.push(`Eligibility: ${reasonLabel(item.eligibility_state)}`);
  if (item.requirement_filter_state) details.push(`Requirement fit: ${reasonLabel(item.requirement_filter_state)}`);
  if (!eligibilityVerified(item)) {
    (item.academic_confidence?.unresolved || []).slice(0, 3)
      .forEach((reason) => details.push(`Unresolved: ${reasonLabel(reason)}`));
  }
  const evidence = item.preference_match?.matched_preferences || [];
  evidence.slice(0, 3).forEach((match) => {
    const value = match.original_value || match.value;
    if (value) details.push(`Matched preference: ${value}`);
  });
  const sourceEvidence = [
    ...(item.preference_match?.positive_evidence || []),
    ...evidence.flatMap((match) => match.evidence || []),
  ];
  sourceEvidence.slice(0, 2).forEach((entry) => {
    if (entry?.matched_text) details.push(`Course evidence: ${entry.matched_text}`);
  });
  if (details.length) {
    const list = document.createElement("ul");
    list.className = "course-evidence";
    details.forEach((detail) => {
      const entry = document.createElement("li");
      entry.textContent = detail;
      list.append(entry);
    });
    card.append(list);
  }
  return card;
}

function renderCourseGroup(container, items, weakMatch = false) {
  container.replaceChildren();
  const limit = weakMatch ? RELATED_DISPLAY_LIMIT : items.length;
  const visible = items.slice(0, limit);
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const title = document.createElement("strong");
    title.textContent = weakMatch ? "No weaker matches to show." : "No matching courses yet.";
    const copy = document.createElement("p");
    copy.textContent = weakMatch
      ? "Every course we found matches your interests directly."
      : "No course description matched your interests closely enough. Try describing what you want to study in different words.";
    empty.append(title, copy);
    container.append(empty);
    return;
  }
  visible.forEach((item) => container.append(resultCard(item, weakMatch)));
  if (items.length > limit) {
    const remaining = document.createElement("p");
    remaining.className = "result-note";
    remaining.textContent = `Showing ${limit} of ${items.length} weaker matches. The full set remains available in the API result.`;
    container.append(remaining);
  }
}

function usedFallback(result) {
  const uninterpreted = result.intent?.uninterpreted || [];
  const deterministicExplanations = [
    ...(result.recommended_courses || []),
    ...(result.related_courses || []),
  ].some((item) => item.explanation?.method === "deterministic");
  return uninterpreted.some((item) => item?.reason === "gemini_unavailable_or_invalid") || deterministicExplanations;
}

function renderRecommendations(result) {
  const summary = result.summary || {};
  const recommended = result.recommended_courses || [];
  const related = result.related_courses || [];
  const verified = summary.eligibility_verified_count || 0;
  resultsSummary.textContent =
    `${recommended.length} recommended · ${verified} with eligibility verified · ${related.length} weaker matches`;
  recommendedCount.textContent = `${recommended.length}`;
  relatedCount.textContent = `${related.length}`;
  renderCourseGroup(recommendedResults, recommended);
  renderCourseGroup(relatedResults, related, true);
  if (usedFallback(result)) {
    resultNotice.textContent = "AI assistance was unavailable for part of this result, so deterministic matching or grounded explanations were used. Academic safety checks were unchanged.";
    resultNotice.hidden = false;
  } else {
    resultNotice.hidden = true;
  }
  resultsPanel.hidden = false;
  resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setLoading(loading) {
  recommendButton.disabled = loading;
  recommendButton.classList.toggle("loading", loading);
  recommendationForm.setAttribute("aria-busy", String(loading));
  if (loading) {
    recommendationStatus.textContent = "Checking academic requirements and matching your preferences…";
  }
}

recommendationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showIssues([]);
  if (!recommendationForm.checkValidity()) {
    recommendationForm.reportValidity();
    recommendationStatus.textContent = "Complete the required details to continue.";
    return;
  }
  const localIssues = localValidationIssues();
  if (localIssues.length) {
    showIssues(localIssues);
    recommendationStatus.textContent = "Review the highlighted details.";
    return;
  }
  setLoading(true);
  try {
    const response = await fetch("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        profile: profilePayload(),
        query: document.querySelector("#preference-query").value.trim(),
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!payload.recommendations) {
      throw new Error(payload.message || "The recommendation service returned an unexpected response.");
    }
    const result = payload.recommendations;
    if (!response.ok || result.validation?.is_valid !== true) {
      const issues = result.validation?.issues || [];
      showIssues(issues.length ? issues : [{ message: "The academic profile could not be validated." }]);
      recommendationStatus.textContent = "Review your profile before requesting recommendations.";
      return;
    }
    renderRecommendations(result);
    recommendationStatus.textContent = "Recommendation analysis is complete.";
  } catch (error) {
    showIssues([{ message: error.message }], "Recommendations could not be loaded:");
    recommendationStatus.textContent = "Something went wrong. Your selections are still here; please try again.";
  } finally {
    setLoading(false);
  }
});

loadDashboardOptions();
