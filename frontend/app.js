const statusElement = document.querySelector("#api-status");
const detailElement = document.querySelector("#connection-detail");
const profileForm = document.querySelector("#student-profile-form");
const formSummary = document.querySelector("#form-summary");
const profileStatus = document.querySelector("#profile-status");
const preferenceForm = document.querySelector("#preference-query-form");
const preferenceStatus = document.querySelector("#preference-status");
const preferenceResult = document.querySelector("#preference-result");

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
    preferenceStatus.textContent = response.ok
      ? "Recommendation analysis is complete."
      : "The recommendation pipeline needs review.";
  } catch (error) {
    preferenceStatus.textContent = "Preferences could not be interpreted. Try again.";
    preferenceResult.textContent = error.message;
    preferenceResult.hidden = false;
  }
});
