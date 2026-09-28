const statusElement = document.querySelector("#api-status");
const detailElement = document.querySelector("#connection-detail");
const profileForm = document.querySelector("#student-profile-form");
const formSummary = document.querySelector("#form-summary");
const profileStatus = document.querySelector("#profile-status");
const preferenceForm = document.querySelector("#preference-query-form");
const preferenceStatus = document.querySelector("#preference-status");
const preferenceResult = document.querySelector("#preference-result");
const resultsPanel = document.querySelector("#recommendation-results");
const resultsSummary = document.querySelector("#results-summary");
const academicProgress = document.querySelector("#academic-progress");
const confirmedResults = document.querySelector("#confirmed-results");
const verificationResults = document.querySelector("#verification-results");
const DISPLAY_LIMIT = 12;

async function checkApiConnection() {
  try {
    const response = await fetch("/api/health", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`Health check failed: ${response.status}`);
    const health = await response.json();
    if (health.status !== "ok") throw new Error("API reported an unhealthy state");
    statusElement.textContent = "API connected";
    statusElement.classList.add("connected");
    detailElement.textContent = `Connected to ${health.service} (${health.api_version}).`;
  } catch (error) {
    statusElement.textContent = "API unavailable";
    statusElement.classList.add("unavailable");
    detailElement.textContent = "Start the local API service and refresh this page.";
    console.error(error);
  }
}

checkApiConnection();

function courseEntries(value) {
  return value.split(/[\n,]+/).map((code) => code.trim()).filter(Boolean)
    .map((courseCode) => ({ course_code: courseCode }));
}

function profilePayload() {
  return {
    programme: document.querySelector("#programme").value.trim(),
    second_programme: document.querySelector("#second-programme").value.trim() || null,
    current_academic_year: Number(document.querySelector("#academic-year").value),
    current_semester: Number(document.querySelector("#semester").value),
    completed_courses: courseEntries(document.querySelector("#completed-courses").value),
    ongoing_courses: courseEntries(document.querySelector("#ongoing-courses").value),
  };
}

function showIssues(issues) {
  formSummary.replaceChildren();
  if (!issues.length) {
    formSummary.hidden = true;
    return;
  }
  const heading = document.createElement("strong");
  heading.textContent = "Please review the profile:";
  const list = document.createElement("ul");
  issues.forEach((issue) => {
    const item = document.createElement("li");
    item.textContent = issue.message;
    list.append(item);
  });
  formSummary.append(heading, list);
  formSummary.hidden = false;
}

function addProgressMetric(label, value) {
  const card = document.createElement("div");
  card.className = "progress-card";
  const number = document.createElement("strong");
  number.textContent = String(value ?? 0);
  const caption = document.createElement("span");
  caption.textContent = label;
  card.append(number, caption);
  academicProgress.append(card);
}

function reasonLabel(reason) {
  return String(reason || "verification required").replaceAll("_", " ");
}

function resultCard(item, verification = false) {
  const card = document.createElement("article");
  card.className = `course-card ${verification ? "needs-verification" : "confirmed"}`;
  const heading = document.createElement("h4");
  heading.textContent = [item.course_code, item.course_title].filter(Boolean).join(" — ");
  const status = document.createElement("p");
  status.className = "course-status";
  status.textContent = verification ? "Verification required" : "Confirmed";
  const explanation = document.createElement("p");
  explanation.textContent = item.explanation?.text || "No explanation is available.";
  card.append(heading, status, explanation);

  const details = [
    item.eligibility_state && `Eligibility: ${reasonLabel(item.eligibility_state)}`,
    item.requirement_filter_state &&
      `Requirement status: ${reasonLabel(item.requirement_filter_state)}`,
  ].filter(Boolean);
  if (verification) {
    details.push(...(item.reasons || []).map((reason) => `Reason: ${reasonLabel(reason)}`));
  }
  const evidence = item.preference_match?.matched_preferences || [];
  evidence.forEach((match) => details.push(`Preference match: ${match.original_value || match.value}`));
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

function renderCourseGroup(container, items, verification = false) {
  container.replaceChildren();
  const visible = items.slice(0, DISPLAY_LIMIT);
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = verification
      ? "No courses currently require verification."
      : "No courses are confirmed under the available academic evidence.";
    container.append(empty);
    return;
  }
  visible.forEach((item) => container.append(resultCard(item, verification)));
  if (items.length > DISPLAY_LIMIT) {
    const remaining = document.createElement("p");
    remaining.className = "result-note";
    remaining.textContent = `${items.length - DISPLAY_LIMIT} additional courses are retained in the API response.`;
    container.append(remaining);
  }
}

function renderRecommendations(result) {
  const summary = result.summary;
  const academic = result.academic_requirements?.summary || {};
  resultsSummary.textContent = [
    `${summary.confirmed_recommendation_count} confirmed`,
    `${summary.verification_required_count} requiring verification`,
    `${summary.excluded_course_count} excluded`,
  ].join(" · ");
  academicProgress.replaceChildren();
  addProgressMetric("Completed courses", academic.completed_course_count);
  addProgressMetric("Remaining requirements", academic.remaining_requirement_count);
  addProgressMetric("Satisfied requirements", academic.satisfied_requirement_count);
  addProgressMetric("Unevaluable requirements", academic.unevaluable_requirement_count);
  renderCourseGroup(confirmedResults, result.confirmed_recommendations || []);
  renderCourseGroup(verificationResults, result.verification_required || [], true);
  resultsPanel.hidden = false;
  resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

profileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showIssues([]);
  if (!profileForm.checkValidity()) {
    profileForm.reportValidity();
    profileStatus.textContent = "Complete the required fields.";
    return;
  }
  profileStatus.textContent = "Validating profile…";
  try {
    const response = await fetch("/api/student-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(profilePayload()),
    });
    const result = await response.json();
    if (!result.profile) throw new Error(result.message || "Profile validation failed");
    const issues = result.profile.validation.issues || [];
    showIssues(issues);
    if (result.profile.validation.is_valid) {
      profileStatus.textContent = "Profile is valid and ready for academic analysis.";
    } else {
      profileStatus.textContent = "Profile contains validation errors.";
    }
  } catch (error) {
    profileStatus.textContent = "The profile could not be validated. Try again.";
    showIssues([{ message: error.message }]);
  }
});

preferenceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!preferenceForm.checkValidity()) {
    preferenceForm.reportValidity();
    preferenceStatus.textContent = "Enter your course preferences.";
    return;
  }
  preferenceStatus.textContent = "Understanding preferences…";
  preferenceResult.hidden = true;
  try {
    const query = document.querySelector("#preference-query").value;
    if (!profileForm.checkValidity()) {
      profileForm.reportValidity();
      throw new Error("Complete the student profile before requesting recommendations.");
    }
    const response = await fetch("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ profile: profilePayload(), query }),
    });
    const result = await response.json();
    if (!result.recommendations) {
      throw new Error(result.message || "Recommendation pipeline failed");
    }
    const summary = result.recommendations.summary;
    preferenceResult.textContent = [
      `${summary.confirmed_recommendation_count} confirmed`,
      `${summary.verification_required_count} requiring verification`,
      `${summary.excluded_course_count} excluded`,
    ].join(" · ");
    preferenceResult.hidden = false;
    renderRecommendations(result.recommendations);
    preferenceStatus.textContent = response.ok
      ? "Recommendation analysis is complete."
      : "The recommendation pipeline needs review.";
  } catch (error) {
    preferenceStatus.textContent = "Preferences could not be interpreted. Try again.";
    preferenceResult.textContent = error.message;
    preferenceResult.hidden = false;
  }
});
