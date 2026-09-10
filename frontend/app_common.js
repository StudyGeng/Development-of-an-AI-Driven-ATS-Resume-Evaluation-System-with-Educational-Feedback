(function () {
  const AUTH_USER_KEY = "authUser";

  function getAuthUser() {
    try {
      const raw = localStorage.getItem(AUTH_USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function setAuthUser(user, onChange) {
    if (user) {
      localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(AUTH_USER_KEY);
    }
    if (typeof onChange === "function") {
      onChange();
    }
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
    getPasswordValidationMessage,
    sanitizePinInput,
    escapeHtml,
    clearProfileSensitiveFields,
    populateProfileForm,
    setModalOpen,
    getHistoryKey,
    loadHistory,
    saveHistory,
    getAnalysisHistoryKey,
    dedupeHistoryItems,
    saveAnalysisToHistory,
    createHistoryModal
  };
})();
