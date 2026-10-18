(function () {
  const AUTH_USER_KEY = "authUser";
  const UNSAFE_HTTP_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
  const nativeFetch = window.fetch.bind(window);
  let csrfToken = null;
  let csrfTokenRequest = null;
  let authStateVersion = 0;
  let authSessionRequest = null;
  let authSessionVerified = false;

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
        authSessionVerified = true;
        return user;
      })
      .catch(() => {
        authSessionVerified = false;
        return getAuthUser();
      })
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

  function getToastMessage(message, isError = false) {
    if (!isError) {
      return message;
    }
    return /try again/i.test(message) ? message : message + " Please try again.";
  }

  function showToast(message, type = "success") {
    if (!message) {
      return;
    }
    let toastStack = document.getElementById("appToastStack");
    if (!toastStack) {
      toastStack = document.createElement("div");
      toastStack.id = "appToastStack";
      toastStack.className = "toast-stack";
      toastStack.setAttribute("aria-live", "polite");
      document.body.appendChild(toastStack);
    }

    const toast = document.createElement("div");
    toast.className = "app-toast app-toast-" + type;

    const icon = document.createElement("span");
    icon.className = "app-toast-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = type === "error" ? "!" : type === "info" ? "i" : "✓";

    const messageText = document.createElement("span");
    messageText.textContent = message;

    toast.appendChild(icon);
    toast.appendChild(messageText);
    toastStack.appendChild(toast);

    window.setTimeout(() => {
      toast.classList.add("app-toast-hide");
      toast.addEventListener("animationend", () => toast.remove(), { once: true });
    }, 3800);
  }

  async function requestAuthPin(email, purpose, button, messageSetter, apiBaseUrl) {
    const cleanEmail = (email || "").trim().toLowerCase();
    if (!cleanEmail) {
      throw new Error("Enter your email address first.");
    }
    if (button) {
      button.disabled = true;
      button.textContent = "Sending...";
    }
    try {
      const response = await fetch(apiBaseUrl + "/auth/pin/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: cleanEmail, purpose })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "Could not send PIN");
      }
      const devPin = payload.dev_pin ? " Dev PIN: " + payload.dev_pin : "";
      messageSetter("PIN sent to " + cleanEmail + ". It expires in " + payload.expires_in_minutes + " minutes." + devPin);
      showToast("PIN sent successfully. Check your email.", "success");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Send PIN";
      }
    }
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
        experience_markers: ["cloud deployment project", "backend services project"]
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
      recommendation_module: {
        status: "ready",
        recommended_role: "Computer Programmer",
        summary: "Fictional sample guidance: make the project evidence more specific, then check each edit against real experience before using it.",
        priority_actions: [
          {
            priority: 1,
            category: "impact",
            title: "Show the result of the cloud project",
            detail: "The sample resume names Docker and Kubernetes but gives no measurable project outcome.",
            action: "If the student measured deployment time, reliability, or usage, add the verified number to the project bullet.",
            keywords: ["docker", "kubernetes"],
            source: "fictional_sample"
          },
          {
            priority: 2,
            category: "experience",
            title: "Make the API contribution specific",
            detail: "The sample mentions backend services and API testing without describing the student's own contribution.",
            action: "Name the feature or endpoint the student built, the tools used, and the test performed, if those details are true.",
            keywords: ["api", "testing"],
            source: "fictional_sample"
          },
          {
            priority: 3,
            category: "role_fit",
            title: "Connect evidence to the target role",
            detail: "The sample targets Computer Programmer and already shows systems and deployment skills.",
            action: "Keep the most relevant project bullets near the top and explain how they demonstrate programming work.",
            keywords: ["systems design", "programming"],
            source: "fictional_sample"
          }
        ],
        resume_generation_strategy: {
          target_role: "Computer Programmer",
          strategy_summary: "Fictional example only. Keep proven skills and replace example details with the student's own evidence.",
          keyword_targets: [],
          evidence_to_keep: ["systems design", "kubernetes", "docker"]
        }
      },
      generated_at: new Date().toISOString()
    };
  }

  function writeSampleAnalysisForDemo() {
    const sample = createSampleAnalysis();
    try {
      // Keep a user's most recent real analysis available when they preview the demo.
      // The results page uses its explicit sample URL to choose this record.
      localStorage.setItem("sampleAnalysis", JSON.stringify(sample));
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

  function createAccountWorkspace(options) {
    const modal = options.modal;
    const buttons = modal ? Array.from(modal.querySelectorAll("[data-account-view]")) : [];
    const goButtons = modal ? Array.from(modal.querySelectorAll("[data-account-go]")) : [];
    const panels = modal ? Array.from(modal.querySelectorAll("[data-account-panel]")) : [];
    const apiBaseUrl = options.apiBaseUrl || (window.location.protocol.startsWith("http") ? window.location.origin : "http://127.0.0.1:5000");
    const nameForm = modal && modal.querySelector("#profileNameForm");
    const nameInput = modal && modal.querySelector("#profileNameInput");
    const nameMessage = modal && modal.querySelector("#profileNameMessage");
    const avatarInput = modal && modal.querySelector("#profileAvatarInput");
    const chooseAvatarButton = modal && modal.querySelector("#chooseProfileAvatarBtn");
    const avatarMessage = modal && modal.querySelector("#profileAvatarMessage");
    const removeAvatarButton = modal && modal.querySelector("#removeProfileAvatarBtn");
    const emailForm = modal && modal.querySelector("#profileEmailChangeForm");
    const newEmailInput = modal && modal.querySelector("#profileNewEmailInput");
    const emailPasswordInput = modal && modal.querySelector("#profileEmailPasswordInput");
    const emailPinInput = modal && modal.querySelector("#profileEmailPinInput");
    const sendEmailPinButton = modal && modal.querySelector("#sendProfileEmailPinBtn");
    const emailMessage = modal && modal.querySelector("#profileEmailMessage");
    const emailChangeCard = modal && modal.querySelector("#profileEmailChangeCard");
    const adminEmailNote = modal && modal.querySelector("#profileAdminEmailNote");
    const emailInfoNote = modal && modal.querySelector("#profileEmailInfoNote");
    let hasAccount = false;
    let currentView = "info";
    let currentUser = null;
    let avatarEmail = "";
    let avatarRequestId = 0;
    let avatarObjectUrl = null;

    function showMessage(element, message, isError = false) {
      if (!element) return;
      element.textContent = message;
      element.hidden = !message;
      element.classList.toggle("is-error", isError);
    }

    function responseError(payload, fallback) {
      if (typeof payload.detail === "string") return payload.detail;
      if (Array.isArray(payload.detail)) return payload.detail.map((item) => item.msg || String(item)).join(" ");
      return fallback;
    }

    function renderAvatarInitials() {
      const name = currentUser ? String(currentUser.full_name || "Your account") : "Guest";
      const initials = name.trim().split(/\s+/).slice(0, 2).map((part) => part.charAt(0)).join("").toUpperCase() || "U";
      modal.querySelectorAll("[data-account-avatar]").forEach((element) => { element.textContent = initials; });
    }

    async function refreshAvatar() {
      avatarRequestId += 1;
      const requestId = avatarRequestId;
      if (avatarObjectUrl) {
        URL.revokeObjectURL(avatarObjectUrl);
        avatarObjectUrl = null;
      }
      renderAvatarInitials();
      if (removeAvatarButton) removeAvatarButton.hidden = true;
      if (!currentUser) return;
      try {
        const response = await fetch(apiBaseUrl + "/users/me/avatar?v=" + requestId, {
          credentials: "same-origin",
          cache: "no-store"
        });
        if (!response.ok || requestId !== avatarRequestId) return;
        const blob = await response.blob();
        if (requestId !== avatarRequestId) return;
        avatarObjectUrl = URL.createObjectURL(blob);
        modal.querySelectorAll("[data-account-avatar]").forEach((element) => {
          const image = document.createElement("img");
          image.alt = "";
          image.src = avatarObjectUrl;
          element.replaceChildren(image);
        });
        if (removeAvatarButton) removeAvatarButton.hidden = false;
      } catch (error) {
        // Initials remain visible when there is no photo or the connection fails.
      }
    }

    function clearSensitiveFields() {
      if (newEmailInput) newEmailInput.value = "";
      if (emailPasswordInput) emailPasswordInput.value = "";
      if (emailPinInput) emailPinInput.value = "";
      showMessage(emailMessage, "");
      showMessage(nameMessage, "");
      showMessage(avatarMessage, "");
    }

    function show(view) {
      const allowedViews = hasAccount ? ["info", "settings", "history"] : ["access", "history"];
      currentView = allowedViews.includes(view) ? view : (hasAccount ? "info" : "access");
      buttons.forEach((button) => {
        const active = button.dataset.accountView === currentView;
        button.classList.toggle("is-active", active);
        if (active) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      });
      panels.forEach((panel) => { panel.hidden = panel.dataset.accountPanel !== currentView; });
      if (currentView === "history" && typeof options.onHistory === "function") options.onHistory();
    }

    function setUser(user) {
      hasAccount = Boolean(user);
      currentUser = user || null;
      const name = user ? String(user.full_name || "Your account") : "Guest";
      const email = user ? String(user.email || "") : "Account access and history";
      if (modal) {
        const workspace = modal.querySelector(".account-workspace");
        if (workspace) workspace.classList.toggle("is-guest", !hasAccount);
        modal.querySelectorAll("[data-account-name]").forEach((element) => { element.textContent = name; });
        modal.querySelectorAll("[data-account-email]").forEach((element) => { element.textContent = email; });
        modal.querySelectorAll("[data-account-auth-only]").forEach((element) => { element.hidden = !hasAccount; });
        modal.querySelectorAll("[data-account-guest-only]").forEach((element) => { element.hidden = hasAccount; });
        const isAdmin = Boolean(user && user.is_admin);
        if (emailChangeCard) emailChangeCard.hidden = isAdmin;
        if (adminEmailNote) adminEmailNote.hidden = !isAdmin;
        if (emailInfoNote) emailInfoNote.textContent = isAdmin ? "Administrator email is managed separately." : "Email changes need verification.";
        goButtons.forEach((button) => { if (button.dataset.accountGo === "settings") button.hidden = isAdmin; });
        if (isAdmin) clearSensitiveFields();
        if (email !== avatarEmail) {
          avatarEmail = email;
          refreshAvatar();
        } else if (!avatarObjectUrl) {
          renderAvatarInitials();
        }
      }
      if (!hasAccount && currentView !== "history") show("access");
      else if (hasAccount && currentView === "access") show("info");
    }

    if (nameForm) nameForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!currentUser) return;
      const fullName = nameInput.value.trim();
      if (fullName.length < 2 || fullName.length > 100) return showMessage(nameMessage, "Use a name between 2 and 100 characters.", true);
      const saveButton = nameForm.querySelector('button[type="submit"]');
      saveButton.disabled = true;
      showMessage(nameMessage, "Saving name...");
      try {
        const response = await fetch(apiBaseUrl + "/users/me", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ full_name: fullName })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(responseError(payload, "Could not save your name."));
        if (payload.user) {
          setAuthUser(payload.user);
          nameInput.value = payload.user.full_name || fullName;
        }
        showMessage(nameMessage, "Name updated.");
      } catch (error) {
        showMessage(nameMessage, error.message || "Could not save your name.", true);
      } finally {
        saveButton.disabled = false;
      }
    });

    if (chooseAvatarButton && avatarInput) chooseAvatarButton.addEventListener("click", () => avatarInput.click());
    if (avatarInput) avatarInput.addEventListener("change", async () => {
      const file = avatarInput.files && avatarInput.files[0];
      if (!file || !currentUser) return;
      avatarInput.value = "";
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) return showMessage(avatarMessage, "Choose a JPG, PNG, or WebP image.", true);
      if (file.size > 2 * 1024 * 1024) return showMessage(avatarMessage, "Choose an image 2 MB or smaller.", true);
      if (chooseAvatarButton) chooseAvatarButton.disabled = true;
      showMessage(avatarMessage, "Uploading photo...");
      try {
        const formData = new FormData();
        formData.append("avatar", file);
        const response = await fetch(apiBaseUrl + "/users/me/avatar", {
          method: "PUT",
          credentials: "same-origin",
          body: formData
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(responseError(payload, "Could not upload your photo."));
        await refreshAvatar();
        showMessage(avatarMessage, "Photo updated.");
      } catch (error) {
        showMessage(avatarMessage, error.message || "Could not upload your photo.", true);
      } finally {
        if (chooseAvatarButton) chooseAvatarButton.disabled = false;
      }
    });

    if (removeAvatarButton) removeAvatarButton.addEventListener("click", async () => {
      if (!currentUser) return;
      removeAvatarButton.disabled = true;
      showMessage(avatarMessage, "Removing photo...");
      try {
        const response = await fetch(apiBaseUrl + "/users/me/avatar", { method: "DELETE", credentials: "same-origin" });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(responseError(payload, "Could not remove your photo."));
        await refreshAvatar();
        showMessage(avatarMessage, "Photo removed.");
      } catch (error) {
        showMessage(avatarMessage, error.message || "Could not remove your photo.", true);
      } finally {
        removeAvatarButton.disabled = false;
      }
    });

    if (emailPinInput) emailPinInput.addEventListener("input", () => sanitizePinInput(emailPinInput, true));
    if (newEmailInput) newEmailInput.addEventListener("input", () => { if (emailPinInput) emailPinInput.value = ""; });
    if (sendEmailPinButton) sendEmailPinButton.addEventListener("click", async () => {
      if (!currentUser) return;
      const newEmail = newEmailInput.value.trim().toLowerCase();
      const currentPassword = emailPasswordInput.value;
      if (!newEmailInput.checkValidity()) return showMessage(emailMessage, "Enter a valid new email address.", true);
      if (newEmail === String(currentUser.email || "").toLowerCase()) return showMessage(emailMessage, "Enter a different email address.", true);
      if (!currentPassword) return showMessage(emailMessage, "Enter your current password first.", true);
      sendEmailPinButton.disabled = true;
      showMessage(emailMessage, "Sending a PIN to the new email...");
      try {
        const response = await fetch(apiBaseUrl + "/users/me/email-change/pin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ new_email: newEmail, current_password: currentPassword })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(responseError(payload, "Could not send the verification PIN."));
        const devPin = payload.dev_pin ? " Dev PIN: " + payload.dev_pin : "";
        showMessage(emailMessage, "PIN sent to " + newEmail + ". Enter it below to confirm the change." + devPin);
      } catch (error) {
        showMessage(emailMessage, error.message || "Could not send the verification PIN.", true);
      } finally {
        sendEmailPinButton.disabled = false;
      }
    });

    if (emailForm) emailForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!currentUser) return;
      const newEmail = newEmailInput.value.trim().toLowerCase();
      const currentPassword = emailPasswordInput.value;
      const verificationPin = emailPinInput.value.trim();
      if (!newEmailInput.checkValidity()) return showMessage(emailMessage, "Enter a valid new email address.", true);
      if (newEmail === String(currentUser.email || "").toLowerCase()) return showMessage(emailMessage, "Enter a different email address.", true);
      if (!currentPassword) return showMessage(emailMessage, "Enter your current password first.", true);
      if (!/^[0-9]{6}$/.test(verificationPin)) return showMessage(emailMessage, "Enter the 6-digit PIN sent to your new email.", true);
      const saveButton = emailForm.querySelector('button[type="submit"]');
      saveButton.disabled = true;
      showMessage(emailMessage, "Verifying your new email...");
      try {
        const response = await fetch(apiBaseUrl + "/users/me/email", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ new_email: newEmail, current_password: currentPassword, verification_pin: verificationPin })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(responseError(payload, "Could not change your email."));
        if (payload.user) setAuthUser(payload.user);
        const emailDisplay = modal.querySelector("#profileEmailInput");
        if (emailDisplay) emailDisplay.value = payload.user && payload.user.email ? payload.user.email : newEmail;
        newEmailInput.value = "";
        emailPasswordInput.value = "";
        emailPinInput.value = "";
        showMessage(emailMessage, "Email updated and verified.");
      } catch (error) {
        showMessage(emailMessage, error.message || "Could not change your email.", true);
      } finally {
        saveButton.disabled = false;
      }
    });

    buttons.forEach((button) => button.addEventListener("click", () => show(button.dataset.accountView)));
    goButtons.forEach((button) => button.addEventListener("click", () => {
      show(button.dataset.accountGo);
      if (button.dataset.accountGo === "settings" && newEmailInput) newEmailInput.focus({ preventScroll: true });
    }));
    if (modal) modal.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        const closeButton = modal.querySelector(".account-close");
        if (closeButton) closeButton.click();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(modal.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([tabindex="-1"])'))
        .filter((element) => element.getClientRects().length && !element.closest('[aria-hidden="true"]'));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    });
    return { show, setUser, clearSensitiveFields, refreshAvatar, getCurrentView: () => currentView };
  }

  function createHistoryModal(options) {
    const modal = options.modal;
    const list = options.list;
    let focusBeforeOpen = null;

    function close() {
      if (!modal) {
        return;
      }
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("overflow-hidden");
      if (focusBeforeOpen && document.contains(focusBeforeOpen)) {
        focusBeforeOpen.focus({ preventScroll: true });
      }
    }

    function render() {
      if (!list) {
        return;
      }
      const items = dedupeHistoryItems(loadHistory());
      saveHistory(items.slice(0, 50));
      list.innerHTML = "";
      if (options.clearButton) options.clearButton.disabled = items.length === 0;

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
      focusBeforeOpen = document.activeElement;
      render();
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("overflow-hidden");
      if (options.closeButton) options.closeButton.focus({ preventScroll: true });
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
    isAuthSessionVerified: () => authSessionVerified,
    getPasswordValidationMessage,
    sanitizePinInput,
    escapeHtml,
    getToastMessage,
    showToast,
    requestAuthPin,
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
    createAccountWorkspace,
    createHistoryModal
  };

  document.addEventListener("DOMContentLoaded", () => {
    const passwordInput = document.getElementById("loginPasswordInput");
    const emailInput = document.getElementById("loginEmailInput");
    const toggleButton = document.getElementById("toggleLoginPasswordBtn");
    const authMessage = document.getElementById("authMessage");
    const authModal = document.getElementById("authModal");
    if (!passwordInput || !toggleButton) return;

    const setPasswordVisible = (visible) => {
      passwordInput.type = visible ? "text" : "password";
      toggleButton.textContent = visible ? "Hide" : "Show";
      toggleButton.setAttribute("aria-label", visible ? "Hide password" : "Show password");
      toggleButton.setAttribute("aria-pressed", String(visible));
    };
    toggleButton.addEventListener("click", () => {
      setPasswordVisible(passwordInput.type === "password");
      passwordInput.focus({ preventScroll: true });
    });
    const clearStaleLoginError = () => {
      if (!authMessage || !authMessage.classList.contains("text-error")) return;
      authMessage.textContent = "";
      authMessage.classList.add("hidden");
    };
    passwordInput.addEventListener("input", clearStaleLoginError);
    if (emailInput) emailInput.addEventListener("input", clearStaleLoginError);
    if (authModal) {
      new MutationObserver(() => {
        if (authModal.classList.contains("hidden")) setPasswordVisible(false);
      }).observe(authModal, { attributes: true, attributeFilter: ["class"] });
    }
  });

  // Keep keyboard focus inside the dialog currently shown on any app page.
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || (event.key !== "Escape" && event.key !== "Tab")) return;
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"][aria-hidden="false"]'));
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog || dialog.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      const closeButton = dialog.querySelector('button[id^="close"]');
      if (closeButton) {
        event.preventDefault();
        closeButton.click();
      }
      return;
    }
    const focusable = Array.from(dialog.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
      .filter((item) => item.getClientRects().length && !item.closest('[aria-hidden="true"]'));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });

  // Start reconciliation as early as possible. Pages also listen for the
  // change event so their controls update when the request finishes.
  syncAuthUserFromServer();
})();
