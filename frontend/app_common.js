(function () {
  const AUTH_USER_KEY = "authUser";
  const UNSAFE_HTTP_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
  const nativeFetch = window.fetch.bind(window);
  let csrfToken = null;
  let csrfTokenRequest = null;
  let authStateVersion = 0;
  let authSessionRequest = null;

  function getRequestUrl(input) {
    if (typeof input === "string" || input instanceof URL) {
      return new URL(input, window.location.href);
    }
    if (typeof Request !== "undefined" && input instanceof Request) {
      return new URL(input.url, window.location.href);
    }
    return null;
  }

  function isSameOriginRequest(input) {
    const requestUrl = getRequestUrl(input);
    return Boolean(requestUrl && requestUrl.origin === window.location.origin);
  }

  function getRequestMethod(input, options) {
    if (options && options.method) {
      return String(options.method).toUpperCase();
    }
    if (typeof Request !== "undefined" && input instanceof Request) {
      return input.method.toUpperCase();
    }
    return "GET";
  }

  async function loadCsrfToken() {
    if (csrfToken) {
      return csrfToken;
    }
    if (!csrfTokenRequest) {
      csrfTokenRequest = nativeFetch("/auth/csrf-token", {
        credentials: "same-origin",
        cache: "no-store"
      })
        .then(async (response) => {
          const payload = await response.json().catch(() => ({}));
          if (!response.ok || !payload.csrf_token) {
            throw new Error(payload.detail || "Could not establish a secure browser session.");
          }
          csrfToken = payload.csrf_token;
          return csrfToken;
        })
        .finally(() => {
          csrfTokenRequest = null;
        });
    }
    return csrfTokenRequest;
  }

  async function fetchWithCsrfToken(input, options = {}) {
    const token = await loadCsrfToken();
    const inheritedHeaders = typeof Request !== "undefined" && input instanceof Request
      ? input.headers
      : undefined;
    const headers = new Headers(options.headers || inheritedHeaders || {});
    headers.set("X-CSRF-Token", token);
    return nativeFetch(input, { ...options, headers });
  }

  async function secureFetch(input, options = {}) {
    const method = getRequestMethod(input, options);
    if (!UNSAFE_HTTP_METHODS.has(method) || !isSameOriginRequest(input)) {
      return nativeFetch(input, options);
    }

    let response = await fetchWithCsrfToken(input, options);
    if (response.status !== 403) {
      return response;
    }

    const payload = await response.clone().json().catch(() => ({}));
    if (payload.detail !== "CSRF validation failed.") {
      return response;
    }

    // Login and logout rotate the signed session cookie, which also rotates its CSRF token.
    csrfToken = null;
    response = await fetchWithCsrfToken(input, options);
    return response;
  }

  window.fetch = secureFetch;

  function getAuthUser() {
    try {
      const raw = localStorage.getItem(AUTH_USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function setAuthUser(user, onChange) {
    authStateVersion += 1;
    if (user) {
      localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(AUTH_USER_KEY);
    }
    if (typeof onChange === "function") {
      onChange();
    }
    window.dispatchEvent(new CustomEvent("career-navigator-auth-changed", {
      detail: { user: user || null }
    }));
  }

  function syncAuthUserFromServer() {
    if (!window.location.protocol.startsWith("http")) {
      return Promise.resolve(getAuthUser());
    }
    if (authSessionRequest) {
      return authSessionRequest;
    }

    const startingVersion = authStateVersion;
    authSessionRequest = nativeFetch("/auth/session", {
      credentials: "same-origin",
      cache: "no-store"
    })
      .then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || "Could not validate the current session.");
        }

        // Do not overwrite a login/logout that completed while this GET was in flight.
        if (startingVersion !== authStateVersion) {
          return getAuthUser();
        }
        const user = payload.authenticated && payload.user ? payload.user : null;
        setAuthUser(user);
        return user;
      })
      .catch(() => getAuthUser())
      .finally(() => {
        authSessionRequest = null;
      });
    return authSessionRequest;
  }

  function getPasswordValidationMessage(password) {
    if (!password || password.length < 8) {
      return "Password must be at least 8 characters.";
    }
    if (!/[A-Za-z]/.test(password)) {
      return "Password must include at least 1 letter.";
    }
    if (!/\d/.test(password)) {
      return "Password must include at least 1 number.";
    }
    if (!/[^A-Za-z0-9]/.test(password)) {
      return "Password must include at least 1 special character, for example !, @, #, or $.";
    }
    return "";
  }

  function sanitizePinInput(input, clearMixedText = false) {
    if (!input) {
      return;
    }
    const rawValue = input.value || "";
    if (clearMixedText && /\D/.test(rawValue)) {
      input.value = "";
      return;
    }
    const digitsOnly = rawValue.replace(/\D/g, "").slice(0, 6);
    if (rawValue !== digitsOnly) {
      input.value = digitsOnly;
    }
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    }[character]));
  }

  function clearProfileSensitiveFields(elements) {
    if (elements.passwordInput) elements.passwordInput.value = "";
    if (elements.pinInput) elements.pinInput.value = "";
    if (elements.message) {
      elements.message.classList.add("hidden");
      elements.message.textContent = "";
    }
  }

  function populateProfileForm(user, elements) {
    if (!user) {
      return;
    }
    if (elements.nameInput) elements.nameInput.value = user.full_name || "";
    if (elements.emailInput) elements.emailInput.value = user.email || "";
    clearProfileSensitiveFields(elements);
  }

  function setModalOpen(modal, isOpen) {
    if (!modal) {
      return;
    }
    modal.classList.toggle("hidden", !isOpen);
    modal.setAttribute("aria-hidden", isOpen ? "false" : "true");
    document.body.classList.toggle("overflow-hidden", isOpen);
  }

  function createSampleAnalysis() {
    return {
      analysis_title: "UTS CS Career Navigator Resume Analyzer",
      uploaded_resume_name: "sample_resume.pdf",
      matching_percentage: 87,
      resume_excerpt: "Sample resume excerpt showing strong systems design and Kubernetes experience.",
      resume_text_for_generation: "Alex Tan alex.tan@example.com +60 12-345 6789 linkedin.com/in/alextan github.com/alextan Bachelor of Computer Science. Projects: Designed a cloud deployment project using Docker and Kubernetes. Built backend services and tested API workflows. Improved documentation for a software engineering capstone project.",
      resume_summary: {
        skills: ["systems design", "kubernetes", "docker"],
        education_markers: ["bachelor"],
        experience_markers: ["internship"]
      },
      resume_quality: {
        quality_score: 88,
        risk_level: "Low",
        summary: "ATS quality risk is low. The resume format looks readable and structured.",
        criteria: [
          {
            category: "Format",
            status: "pass",
            title: "Readable resume text detected",
            detail: "The uploaded resume contains text that the parser can extract.",
            suggestion: "Keep using a text-based PDF or a clear, high-resolution resume image."
          },
          {
            category: "Impact",
            status: "warning",
            title: "Limited measurable impact",
            detail: "The resume could use more quantified outcomes.",
            suggestion: "Add users, speed, accuracy, revenue, or time improvements."
          }
        ],
        issues: []
      },
      job_description_analysis: {
        important_keywords: ["kubernetes", "systems design", "docker"],
        missing_keywords: []
      },
      compatibility: {
        matching_percentage: 87,
        keyword_match: 87,
        role_evidence: 92,
        resume_quality: 88,
        experience_alignment: 84,
        academic_rigor: 80
      },
      score_breakdown: {
        ats_score: 87,
        keyword_coverage: 87,
        role_evidence: 92,
        experience_alignment: 84,
        academic_rigor: 80,
        resume_quality: 88,
        tfidf_similarity: 72,
        formula: "30% database requirement coverage + 25% role evidence + 15% experience evidence + 20% ATS format quality + 7% education/skills evidence + 3% TF-IDF similarity, with a strong-evidence calibration floor"
      },
      feedback: "Strong keyword match. Quantify your impact in cloud projects.",
      structured_feedback: ["Strong keyword match.", "Quantify impact in cloud projects."],
      summary: "Sample analysis for demo",
      recommendation: "Highlight measurable outcomes in project bullets.",
      target_role: "Computer Programmer",
      selected_role: "Software Engineer",
      role_recommendation: {
        selected_role: "Software Engineer",
        selected_database_role: "Computer Programmer",
        detected_database_role: "Computer Programmer",
        recommended_role: "Computer Programmer",
        recommended_database_role: "Computer Programmer",
        is_different_from_selected: false,
        selected_score: 87,
        recommended_score: 87,
        matched_resume_signals: ["systems", "design", "kubernetes", "docker"],
        recommended_missing_keywords: [],
        selected_missing_keywords: [],
        reason_summary: "The selected role maps to Computer Programmer, and it remains the best database match for this resume.",
        top_role_matches: [
          {
            rank: 1,
            role: "Computer Programmer",
            score: 87,
            confidence_label: "Strong evidence",
            matched_keywords: ["systems", "design", "kubernetes", "docker"],
            missing_keywords: [],
            is_selected_database_role: true,
            is_final_recommended: true,
            reason: "Strong evidence: systems, design, kubernetes, docker."
          },
          {
            rank: 2,
            role: "Software Developer",
            score: 79,
            confidence_label: "Strong evidence",
            matched_keywords: ["software", "development", "testing", "deployment"],
            missing_keywords: ["debugging", "api"],
            is_selected_database_role: false,
            is_final_recommended: false,
            reason: "Strong evidence: software, development, testing, deployment. Improve with: debugging, API."
          },
          {
            rank: 3,
            role: "DevOps Engineer",
            score: 68,
            confidence_label: "Good evidence",
            matched_keywords: ["kubernetes", "docker", "cloud"],
            missing_keywords: ["ci/cd", "monitoring"],
            is_selected_database_role: false,
            is_final_recommended: false,
            reason: "Strong evidence: kubernetes, docker, cloud. Improve with: CI/CD, monitoring."
          }
        ]
      },
      generated_at: new Date().toISOString()
    };
  }

  function writeSampleAnalysisForDemo() {
    const sample = createSampleAnalysis();
    try {
      localStorage.removeItem("analysisResult");
      localStorage.removeItem("analysisOwner");
      localStorage.setItem("sampleAnalysis", JSON.stringify(sample));
      localStorage.setItem("pipelineStatus", "sample_available");
      localStorage.setItem("uploadedResumeName", sample.uploaded_resume_name);
      localStorage.setItem("selectedJobRole", "Sample Role");
      localStorage.setItem("selectedJobDescription", "Sample database role requirements for demo.");
    } catch (error) {
      // The caller can still use the returned sample when storage is unavailable.
    }
    return sample;
  }

  function getHistoryKey() {
    const user = getAuthUser();
    return "analysisHistory_" + (user && user.email ? (user.email || "").toLowerCase() : "guest");
  }

  function loadHistory() {
    try {
      const raw = localStorage.getItem(getHistoryKey()) || "[]";
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }

  function saveHistory(history) {
    try {
      localStorage.setItem(getHistoryKey(), JSON.stringify(history));
    } catch (error) {
      // Local storage can be unavailable in private browsing or strict browser settings.
    }
  }

  function getAnalysisHistoryKey(analysis, role) {
    if (analysis && analysis.analysis_id) {
      return "analysis-id:" + String(analysis.analysis_id);
    }
    return [
      analysis && analysis.uploaded_resume_name ? analysis.uploaded_resume_name : localStorage.getItem("uploadedResumeName") || "resume.pdf",
      role || localStorage.getItem("selectedJobRole") || "",
      analysis && analysis.generated_at ? analysis.generated_at : "",
      analysis && analysis.matching_percentage !== undefined ? String(analysis.matching_percentage) : ""
    ].join("|");
  }

  function dedupeHistoryItems(items) {
    const seen = new Set();
    return items.filter((item) => {
      const key = item.history_key || getAnalysisHistoryKey(item.full || {}, item.role || "");
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      item.history_key = key;
      return true;
    });
  }

  function saveAnalysisToHistory(analysis, role) {
    if (!analysis) {
      return;
    }
    const history = dedupeHistoryItems(loadHistory());
    const selectedRole = role || localStorage.getItem("selectedJobRole") || "";
    const historyKey = getAnalysisHistoryKey(analysis, selectedRole);
    const exists = history.some((item) => (
      item.history_key || getAnalysisHistoryKey(item.full || {}, item.role || "")
    ) === historyKey);

    if (exists) {
      saveHistory(history.slice(0, 50));
      return;
    }

    history.unshift({
      id: Date.now(),
      uploaded_resume_name: analysis.uploaded_resume_name || localStorage.getItem("uploadedResumeName") || "resume.pdf",
      role: selectedRole,
      matching_percentage: analysis.matching_percentage || analysis.matching_percentage === 0
        ? analysis.matching_percentage
        : (analysis.compatibility && analysis.compatibility.matching_percentage) || 0,
      excerpt: analysis.resume_excerpt || "",
      stored_at: new Date().toISOString(),
      history_key: historyKey,
      full: analysis
    });
    saveHistory(history.slice(0, 50));
  }

  function createHistoryModal(options) {
    const modal = options.modal;
    const list = options.list;

    function close() {
      if (!modal) {
        return;
      }
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("overflow-hidden");
    }

    function render() {
      if (!list) {
        return;
      }
      const items = dedupeHistoryItems(loadHistory());
      saveHistory(items.slice(0, 50));
      list.innerHTML = "";

      if (!items.length) {
        const emptyState = document.createElement("div");
        emptyState.className = "p-4 rounded-lg border border-outline-variant/60 bg-surface-container-lowest";
        emptyState.innerHTML = `
          <div class="font-semibold text-primary">No past evaluations found.</div>
          <p class="mt-1 text-sm text-on-surface-variant">${escapeHtml(options.emptyDescription || "Open a sample evaluated resume to explore the complete analysis flow.")}</p>
          <button class="mt-3 px-4 py-2 rounded-lg bg-primary text-on-primary text-sm font-semibold" type="button">View Sample Evaluation</button>`;
        const sampleButton = emptyState.querySelector("button");
        if (sampleButton) {
          sampleButton.addEventListener("click", () => {
            if (typeof options.onEmptyAction === "function") {
              options.onEmptyAction();
            }
            close();
          });
        }
        list.appendChild(emptyState);
        return;
      }

      items.forEach((item) => {
        const element = document.createElement("div");
        element.className = "p-3 rounded-lg border border-outline-variant/60 flex items-center justify-between";
        element.innerHTML = `<div>
          <div class="font-semibold">${escapeHtml(item.uploaded_resume_name)} - ${escapeHtml(item.role || "Role")}</div>
          <div class="text-xs text-on-surface-variant">Score: ${Number(item.matching_percentage).toFixed(0)}/100 | ${escapeHtml(new Date(item.stored_at).toLocaleString())}</div>
        </div>
        <div class="flex gap-2">
          <button class="viewHistoryBtn px-3 py-1 rounded-lg bg-primary text-on-primary text-sm" type="button">View</button>
          <button class="deleteHistoryBtn px-3 py-1 rounded-lg bg-surface-container-low text-primary text-sm" type="button">Delete</button>
        </div>`;

        element.querySelector(".viewHistoryBtn").addEventListener("click", () => {
          if (typeof options.onView === "function") {
            options.onView(item);
          }
          close();
        });
        element.querySelector(".deleteHistoryBtn").addEventListener("click", () => {
          saveHistory(loadHistory().filter((candidate) => candidate.id !== item.id));
          render();
        });
        list.appendChild(element);
      });
    }

    function open() {
      if (!modal) {
        return;
      }
      render();
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("overflow-hidden");
    }

    if (options.openButton) options.openButton.addEventListener("click", open);
    if (options.backdrop) options.backdrop.addEventListener("click", close);
    if (options.closeButton) options.closeButton.addEventListener("click", close);
    if (options.doneButton) options.doneButton.addEventListener("click", close);
    if (options.clearButton) {
      options.clearButton.addEventListener("click", () => {
        saveHistory([]);
        render();
      });
    }

    return { open, close, render };
  }

  window.CareerNavigatorCommon = {
    getAuthUser,
    setAuthUser,
    syncAuthUserFromServer,
    getPasswordValidationMessage,
    sanitizePinInput,
    escapeHtml,
    clearProfileSensitiveFields,
    populateProfileForm,
    setModalOpen,
    createSampleAnalysis,
    writeSampleAnalysisForDemo,
    getHistoryKey,
    loadHistory,
    saveHistory,
    getAnalysisHistoryKey,
    dedupeHistoryItems,
    saveAnalysisToHistory,
    createHistoryModal
  };

  // Start reconciliation as early as possible. Pages also listen for the
  // change event so their controls update when the request finishes.
  syncAuthUserFromServer();
})();
