(function () {
  const AUTH_USER_KEY = "authUser";
  const LOCAL_AUTH_USERS_KEY = "localAuthUsers";
  const INTERNAL_TEST_USER = {
    full_name: "UTS Internal Tester",
    email: "test@uts.local",
    password: "test12345"
  };

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

  function getLocalAuthUsers() {
    try {
      const raw = localStorage.getItem(LOCAL_AUTH_USERS_KEY);
      const users = raw ? JSON.parse(raw) : [];
      return Array.isArray(users) ? users : [];
    } catch (error) {
      return [];
    }
  }

  function saveLocalAuthUsers(users) {
    localStorage.setItem(LOCAL_AUTH_USERS_KEY, JSON.stringify(users));
  }

  function seedLocalAuthUser(authMode) {
    if (authMode !== "internal") {
      return;
    }
    const users = getLocalAuthUsers();
    const exists = users.some((user) => (user.email || "").toLowerCase() === INTERNAL_TEST_USER.email);
    if (exists) {
      return;
    }
    users.push({
      id: Date.now(),
      full_name: INTERNAL_TEST_USER.full_name,
      email: INTERNAL_TEST_USER.email,
      password: INTERNAL_TEST_USER.password,
      created_at: new Date().toISOString()
    });
    saveLocalAuthUsers(users);
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

  window.CareerNavigatorCommon = {
    getAuthUser,
    setAuthUser,
    getLocalAuthUsers,
    saveLocalAuthUsers,
    seedLocalAuthUser,
    getHistoryKey,
    loadHistory,
    saveHistory,
    getAnalysisHistoryKey,
    dedupeHistoryItems,
    saveAnalysisToHistory
  };
})();
