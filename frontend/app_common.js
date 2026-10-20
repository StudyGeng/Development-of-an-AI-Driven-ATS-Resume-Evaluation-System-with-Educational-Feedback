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

  function hasCurrentUserReport() {
    if (!authSessionVerified) return false;
    try {
      const user = getAuthUser();
      const owner = (localStorage.getItem("analysisOwner") || "").toLowerCase();
      const raw = localStorage.getItem("analysisResult");
      if (!user?.email || !owner || owner !== user.email.toLowerCase() || !raw) return false;
      const result = JSON.parse(raw);
      const rawScore = result?.matching_percentage;
      const validScore = (typeof rawScore === "number" || typeof rawScore === "string")
        && String(rawScore).trim() !== "" && Number.isFinite(Number(rawScore))
        && Number(rawScore) >= 0 && Number(rawScore) <= 100;
      const hasEvidence = result && typeof result === "object" && (
        result.resume_summary || result.compatibility || result.role_recommendation
        || result.feedback || result.analysis_id
      );
      const isFictionalSample = result?.summary === "Sample analysis for demo"
        && result.uploaded_resume_name === "sample_resume.pdf";
      return Boolean(validScore && hasEvidence && !isFictionalSample);
    } catch (error) {
      return false;
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

  function isValidEmail(email) {
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(email || "").trim().toLowerCase());
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

  const LANGUAGE_KEY = "careerNavigatorLanguage";
  const translations = {
    "How it works": "Cara ia berfungsi",
    "Sample results": "Contoh hasil",
    "Your report": "Laporan anda",
    "Admin insights": "Wawasan pentadbir",
    "Home": "Laman utama",
    "Results": "Keputusan",
    "Admin Insights": "Wawasan pentadbir",
    "Resume feedback for your next role": "Maklum balas resume untuk peranan anda seterusnya",
    "Make your next resume": "Jadikan resume anda yang seterusnya",
    "stronger.": "lebih mantap.",
    "See how your resume matches the role you want, discover missing evidence, and get clear advice on what to improve.": "Lihat padanan resume anda dengan peranan pilihan, kenal pasti bukti yang kurang dan dapatkan cadangan penambahbaikan yang jelas.",
    "Check my resume": "Semak resume saya",
    "See a sample report": "Lihat contoh laporan",
    "Your latest report is ready": "Laporan terkini anda sudah tersedia",
    "Return directly to the results you were viewing.": "Kembali terus ke keputusan yang anda lihat tadi.",
    "Continue report": "Teruskan laporan",
    "Role-based matching": "Padanan mengikut peranan",
    "ATS format feedback": "Maklum balas format ATS",
    "Practical improvement tips": "Tip penambahbaikan praktikal",
    "From the built-in sample report": "Daripada contoh laporan terbina dalam",
    "Resume feedback": "Maklum balas resume",
    "Sample report · fictional data": "Contoh laporan · data rekaan",
    "Selected role:": "Peranan dipilih:",
    "Database match:": "Padanan pangkalan data:",
    "UTS CS readiness score": "Skor kesediaan UTS CS",
    "Matched evidence": "Bukti yang sepadan",
    "Next edit": "Suntingan seterusnya",
    "Your resume report": "Laporan resume anda",
    "Your resume,": "Resume anda,",
    "clearly reviewed.": "disemak dengan jelas.",
    "Start here": "Mulakan di sini",
    "Your next 3 edits": "3 suntingan seterusnya",
    "Read the full improvement plan": "Baca pelan penambahbaikan penuh",
    "Overview": "Gambaran keseluruhan",
    "Job match": "Padanan kerja",
    "Improve": "Tambah baik",
    "ATS checks": "Semakan ATS",
    "Resume draft": "Draf resume",
    "Priority Improvement Plan": "Pelan Penambahbaikan Utama",
    "Resume strengths": "Kekuatan resume",
    "Download your feedback": "Muat turun maklum balas anda",
    "Download PDF report": "Muat turun laporan PDF",
    "Back to home": "Kembali ke laman utama",
    "No resume analysis yet": "Belum ada analisis resume",
    "Your report starts here": "Laporan anda bermula di sini",
    "Opening your report": "Membuka laporan anda",
    "Checking for a saved analysis...": "Menyemak analisis yang disimpan...",
    "My account": "Akaun saya",
    "Account access": "Akses akaun",
    "Guest": "Tetamu",
    "Your account": "Akaun anda",
    "Sign in or create an account": "Log masuk atau cipta akaun",
    "Sign in": "Log masuk",
    "Sign up": "Daftar",
    "Log in": "Log masuk",
    "Log In": "Log masuk",
    "Create Account": "Cipta akaun",
    "User info": "Maklumat pengguna",
    "Settings": "Tetapan",
    "History": "Sejarah",
    "Log out": "Log keluar",
    "Your profile": "Profil anda",
    "Add a photo and keep the name on your account up to date.": "Tambah foto dan pastikan nama akaun anda terkini.",
    "UTS CS Career Navigator account": "Akaun UTS CS Career Navigator",
    "Upload photo": "Muat naik foto",
    "Remove": "Buang",
    "Full name": "Nama penuh",
    "Verified email": "E-mel yang disahkan",
    "Email": "E-mel",
    "Password": "Kata laluan",
    "New password": "Kata laluan baharu",
    "Current password": "Kata laluan semasa",
    "Save name": "Simpan nama",
    "Email changes need verification.": "Perubahan e-mel memerlukan pengesahan.",
    "Change email in Settings": "Tukar e-mel dalam Tetapan",
    "Account security": "Keselamatan akaun",
    "Manage your verified email and password.": "Urus e-mel yang disahkan dan kata laluan anda.",
    "Change email": "Tukar e-mel",
    "New email": "E-mel baharu",
    "Verification PIN": "PIN pengesahan",
    "Password change PIN": "PIN penukaran kata laluan",
    "Send PIN": "Hantar PIN",
    "Confirm new email": "Sahkan e-mel baharu",
    "Update password": "Kemas kini kata laluan",
    "Forgot password?": "Lupa kata laluan?",
    "Reset Password": "Tetapkan semula kata laluan",
    "Back to Sign in": "Kembali ke Log masuk",
    "Password strength": "Kekuatan kata laluan",
    "Start typing": "Mula menaip",
    "8 or more characters": "8 aksara atau lebih",
    "A letter": "Satu huruf",
    "A number": "Satu nombor",
    "A symbol": "Satu simbol",
    "Open a previous report or remove evaluations you no longer need.": "Buka laporan terdahulu atau buang penilaian yang tidak diperlukan.",
    "Clear history": "Padam sejarah",
    "Done": "Selesai",
    "View report": "Lihat laporan",
    "Delete": "Padam",
    "Welcome to your career workspace.": "Selamat datang ke ruang kerjaya anda.",
    "Sign in to analyze your resume and keep your feedback together, or create a new account to get started.": "Log masuk untuk menganalisis resume dan menyimpan maklum balas, atau cipta akaun baharu untuk bermula.",
    "Ready to make your next move?": "Bersedia untuk langkah seterusnya?",
    "Choose Sign in or Sign up from the account menu.": "Pilih Log masuk atau Daftar daripada menu akaun.",
    "Sign up or log in to continue": "Daftar atau log masuk untuk meneruskan",
    "You need an account before uploading resumes or exporting analysis results.": "Anda memerlukan akaun sebelum memuat naik resume atau mengeksport hasil analisis.",
    "Sign in to continue": "Log masuk untuk meneruskan",
    "Step 1": "Langkah 1",
    "Step 2": "Langkah 2",
    "Upload resume file": "Muat naik fail resume",
    "Choose File": "Pilih fail",
    "Remove file": "Buang fail",
    "Selected document": "Dokumen dipilih",
    "Stop waiting": "Berhenti menunggu",
    "Analyzing resume": "Menganalisis resume",
    "Refresh insights": "Muat semula wawasan",
    "Account options": "Pilihan akaun",
    "Open account options": "Buka pilihan akaun",
    "Close account dialog": "Tutup dialog akaun",
    "Your evaluations": "Penilaian anda",
    "8+ chars, 1 letter, 1 number, 1 special character": "8+ aksara, 1 huruf, 1 nombor, 1 aksara khas",
    "Enter a new password": "Masukkan kata laluan baharu",
    "6-digit PIN": "PIN 6 digit",
    "Your full name": "Nama penuh anda",
    "Your password": "Kata laluan anda",
    "Search database roles or type your own": "Cari peranan atau taip pilihan anda",
    "Search roles": "Cari peranan",
    "A sample report, clearly explained.": "Contoh laporan yang diterangkan dengan jelas.",
    "A score starts the conversation. The details help you decide what to improve next.": "Skor ialah titik permulaan. Butirannya membantu anda memilih penambahbaikan seterusnya.",
    "A stronger bullet starts with what you did.": "Poin yang lebih kuat bermula dengan tindakan anda.",
    "A student resume feedback project at the University of Technology Sarawak, Malaysia.": "Projek maklum balas resume pelajar di University of Technology Sarawak, Malaysia.",
    "About this project": "Tentang projek ini",
    "A PIN will be sent to your current verified email.": "PIN akan dihantar ke e-mel semasa yang disahkan.",
    "Add a resume with readable text for the clearest analysis.": "Tambahkan resume dengan teks yang boleh dibaca untuk analisis yang paling jelas.",
    "Administrator email is managed separately. Contact the system owner if it needs to change.": "E-mel pentadbir diurus secara berasingan. Hubungi pemilik sistem jika perlu diubah.",
    "Administrator email is managed separately.": "E-mel pentadbir diurus secara berasingan.",
    "After analysis, this area will generate a stronger ATS resume draft using the database role fit, missing keywords, and feedback.": "Selepas analisis, bahagian ini akan menghasilkan draf resume ATS yang lebih kuat berdasarkan padanan peranan, kata kunci yang kurang dan maklum balas.",
    "Analysis Snapshot": "Ringkasan analisis",
    "Assessment summary": "Ringkasan penilaian",
    "ATS Feedback": "Maklum balas ATS",
    "ATS Parsing": "Penghuraian ATS",
    "ATS Quality Criteria": "Kriteria kualiti ATS",
    "ATS Readiness Check": "Semakan kesediaan ATS",
    "ATS resume builder": "Pembina resume ATS",
    "ATS Resume Generator": "Penjana resume ATS",
    "ATS Resume Preview": "Pratonton resume ATS",
    "Before you begin": "Sebelum anda bermula",
    "Beyond Keywords": "Lebih daripada kata kunci",
    "Build an improved draft": "Bina draf yang lebih baik",
    "Built for students learning to improve a resume": "Dibina untuk pelajar yang ingin menambah baik resume",
    "Cancel": "Batal",
    "Change password": "Tukar kata laluan",
    "Check your resume": "Semak resume anda",
    "Check the file used for this analysis, build an updated draft, or save the full feedback report.": "Semak fail yang digunakan untuk analisis ini, bina draf terkini atau simpan laporan penuh.",
    "Choose a database role, or keep your own wording as a custom target.": "Pilih peranan pangkalan data atau gunakan peranan anda sendiri.",
    "Choose a role": "Pilih peranan",
    "Choose a role, upload your resume, and see the evidence and improvements in your report.": "Pilih peranan, muat naik resume anda dan lihat bukti serta cadangan dalam laporan.",
    "Choose a target role if you have one, then add a resume. We will show the match evidence and practical improvements.": "Pilih peranan sasaran jika ada, kemudian muat naik resume. Kami akan tunjukkan bukti padanan dan cadangan praktikal.",
    "Choose a resume on the home page to see your role match, ATS checks, and practical next steps. If you have a saved report, sign in and open History. You can also view a clearly labelled sample.": "Pilih resume pada laman utama untuk melihat padanan peranan, semakan ATS dan langkah seterusnya. Jika anda ada laporan tersimpan, log masuk dan buka Sejarah. Anda juga boleh melihat contoh yang dilabel dengan jelas.",
    "Communication, teamwork, and leadership signals.": "Bukti komunikasi, kerja berpasukan dan kepimpinan.",
    "Complete the new password requirements before requesting a PIN.": "Lengkapkan syarat kata laluan baharu sebelum meminta PIN.",
    "Complete your email and new password before requesting a PIN.": "Isi e-mel dan kata laluan baharu sebelum meminta PIN.",
    "Complete your name, email, and password before requesting a PIN.": "Isi nama, e-mel dan kata laluan sebelum meminta PIN.",
    "Connect evidence to the target role": "Kaitkan bukti dengan peranan sasaran",
    "Current selected role": "Peranan semasa yang dipilih",
    "Data use for UTS CS Career Navigator": "Penggunaan data untuk UTS CS Career Navigator",
    "Database comparison": "Perbandingan pangkalan data",
    "Database job roles": "Peranan kerja dalam pangkalan data",
    "Database match:": "Padanan pangkalan data:",
    "Database role:": "Peranan pangkalan data:",
    "Database mapping will appear here.": "Pemetaan pangkalan data akan dipaparkan di sini.",
    "Do not upload confidential or third-party information without permission. This tool is for educational feedback, so review its suggestions before using them in an application.": "Jangan muat naik maklumat sulit atau milik pihak lain tanpa kebenaran. Alat ini memberi maklum balas pendidikan; semak cadangannya sebelum menggunakannya dalam permohonan kerja.",
    "Do not upload confidential or third-party information without permission. Review the feedback before using it in a job application.": "Jangan muat naik maklumat sulit atau milik pihak lain tanpa kebenaran. Semak maklum balas sebelum menggunakannya dalam permohonan kerja.",
    "Do not upload confidential or third-party material unless you have permission to do so.": "Jangan muat naik bahan sulit atau milik pihak lain tanpa kebenaran.",
    "Download PDF": "Muat turun PDF",
    "Drop resume here or choose file": "Letakkan resume di sini atau pilih fail",
    "Education & skills": "Pendidikan & kemahiran",
    "Email verification PIN": "PIN pengesahan e-mel",
    "Enter a new email and your current password to request a PIN.": "Masukkan e-mel baharu dan kata laluan semasa untuk meminta PIN.",
    "Evidence match and ATS feedback. Not a hiring probability.": "Padanan bukti dan maklum balas ATS. Bukan kebarangkalian diterima kerja.",
    "Evidence match and ATS feedback. This is not a hiring probability.": "Padanan bukti dan maklum balas ATS. Ini bukan kebarangkalian diterima kerja.",
    "Evidence-match scores from resume text and database requirements.": "Skor padanan bukti daripada teks resume dan keperluan pangkalan data.",
    "Evidence To Keep": "Bukti yang perlu dikekalkan",
    "Experience": "Pengalaman",
    "Experience Alignment": "Padanan pengalaman",
    "Explore": "Teroka",
    "Explore the feedback": "Teroka maklum balas",
    "Explore the sample report": "Teroka contoh laporan",
    "Feedback": "Maklum balas",
    "Feedback will appear here after analysis.": "Maklum balas akan dipaparkan di sini selepas analisis.",
    "File reviewed": "Fail disemak",
    "File Type": "Jenis fail",
    "Final role used": "Peranan akhir yang digunakan",
    "Final score shown above": "Skor akhir dipaparkan di atas",
    "Fictional example - not for submission.": "Contoh rekaan - bukan untuk dihantar.",
    "Fictional sample": "Contoh rekaan",
    "Full feedback report": "Laporan maklum balas penuh",
    "Generate Optimized Resume": "Jana resume yang dioptimumkan",
    "Generated Draft": "Draf yang dijana",
    "Guidelines for using the UTS CS career tool": "Panduan menggunakan alat kerjaya UTS CS",
    "Hard Skills": "Kemahiran teknikal",
    "How It Is Combined": "Cara skor digabungkan",
    "How the navigator works": "Cara navigator berfungsi",
    "Important keywords: 0": "Kata kunci penting: 0",
    "Improvement advice shown here only, not included in the PDF download": "Cadangan penambahbaikan dipaparkan di sini sahaja, tidak termasuk dalam muat turun PDF",
    "Included in final score": "Termasuk dalam skor akhir",
    "Inside your report": "Dalam laporan anda",
    "Its role comparisons and ATS checks are guidance for revision, not a guarantee of interviews or employment.": "Perbandingan peranan dan semakan ATS memberi panduan semakan, bukan jaminan temu duga atau pekerjaan.",
    "Keep a copy": "Simpan salinan",
    "Keyword Match": "Padanan kata kunci",
    "Keyword targets pending.": "Sasaran kata kunci belum tersedia.",
    "Live score components will appear here.": "Komponen skor akan dipaparkan di sini.",
    "Loading available roles...": "Memuatkan peranan yang tersedia...",
    "Loading role list...": "Memuatkan senarai peranan...",
    "Make the API contribution specific": "Terangkan sumbangan API secara khusus",
    "Matched evidence:": "Bukti yang sepadan:",
    "Matched resume signals": "Bukti resume yang sepadan",
    "Matched evidence pending.": "Bukti yang sepadan belum tersedia.",
    "Matched resume signals will appear here.": "Bukti resume yang sepadan akan dipaparkan di sini.",
    "Methods used in the project": "Kaedah yang digunakan dalam projek",
    "Missing keyword rewrite targets will appear after analysis.": "Sasaran penulisan semula kata kunci akan dipaparkan selepas analisis.",
    "Next Edit": "Suntingan seterusnya",
    "Next Rewrite": "Penulisan semula seterusnya",
    "No file selected.": "Tiada fail dipilih.",
    "No resume selected": "Tiada resume dipilih",
    "No role selected": "Tiada peranan dipilih",
    "No role selected.": "Tiada peranan dipilih.",
    "One final score combines role match, resume evidence, and ATS format quality.": "Satu skor akhir menggabungkan padanan peranan, bukti resume dan kualiti format ATS.",
    "Open the full sample report": "Buka contoh laporan penuh",
    "Or let the tool suggest one.": "Atau biarkan alat mencadangkan peranan.",
    "PDF, PNG, JPG, or JPEG · up to 10 MB": "PDF, PNG, JPG atau JPEG · sehingga 10 MB",
    "Personal improvement plan": "Pelan penambahbaikan peribadi",
    "Pick a role if you have a specific job in mind.": "Pilih peranan jika anda mempunyai pekerjaan khusus dalam fikiran.",
    "Popular market roles": "Peranan popular di pasaran",
    "Practical feedback from your file.": "Maklum balas praktikal daripada fail anda.",
    "Preview report": "Pratonton laporan",
    "Preview of sections in the results page": "Pratonton bahagian pada halaman keputusan",
    "Preview the complete report here, or save a PDF with your ATS checks, matched evidence, and improvement plan.": "Pratonton laporan penuh di sini atau simpan PDF dengan semakan ATS, bukti padanan dan pelan penambahbaikan.",
    "Preview the report interface.": "Pratonton antara muka laporan.",
    "Privacy Policy": "Dasar Privasi",
    "Privacy policy": "Dasar privasi",
    "Project & policies": "Projek & dasar",
    "Project Evidence": "Bukti projek",
    "Projects, internships, and measurable outcomes.": "Projek, latihan industri dan hasil yang boleh diukur.",
    "Put your feedback to work": "Gunakan maklum balas anda",
    "Quality checks will appear here after analysis.": "Semakan kualiti akan dipaparkan di sini selepas analisis.",
    "Recommendation actions will appear after analysis.": "Tindakan cadangan akan dipaparkan selepas analisis.",
    "Recommendation details will appear after analysis.": "Butiran cadangan akan dipaparkan selepas analisis.",
    "Recommended job fit will appear here.": "Cadangan padanan kerja akan dipaparkan di sini.",
    "Research Credits": "Kredit penyelidikan",
    "Research credits": "Kredit penyelidikan",
    "Resume": "Resume",
    "Resume check": "Semakan resume",
    "Resume evidence ranking": "Kedudukan bukti resume",
    "Resume Generator Strategy": "Strategi penjana resume",
    "Resume-generation input will appear after analysis.": "Input penjanaan resume akan dipaparkan selepas analisis.",
    "Review or save your feedback": "Semak atau simpan maklum balas",
    "Role": "Peranan",
    "Role Fit Recommendation": "Cadangan padanan peranan",
    "Role mapping pending.": "Pemetaan peranan belum tersedia.",
    "Role-fit reasoning will appear after analysis.": "Sebab padanan peranan akan dipaparkan selepas analisis.",
    "Run an analysis to check resume format, language, contact details, and parsing risks.": "Jalankan analisis untuk menyemak format resume, bahasa, butiran hubungan dan risiko pembacaan ATS.",
    "Run an analysis to compare the selected job with the best database match.": "Jalankan analisis untuk membandingkan pekerjaan dipilih dengan padanan pangkalan data terbaik.",
    "Run an analysis to see the recommended next actions for the selected role.": "Jalankan analisis untuk melihat tindakan seterusnya yang dicadangkan bagi peranan dipilih.",
    "Run an analysis to see the resume score and role recommendation.": "Jalankan analisis untuk melihat skor resume dan cadangan peranan.",
    "Run an analysis to see what the score means.": "Jalankan analisis untuk memahami maksud skor.",
    "Sample report": "Contoh laporan",
    "Score Components": "Komponen skor",
    "Score conclusion": "Kesimpulan skor",
    "Score, Evidence, and Rewrite Plan": "Skor, bukti dan pelan penulisan semula",
    "Scroll here to review your report. Close this window to return to your results.": "Tatal di sini untuk menyemak laporan. Tutup tetingkap ini untuk kembali ke keputusan.",
    "Search an available role, enter your own, or skip this step.": "Cari peranan tersedia, taip sendiri atau langkau langkah ini.",
    "Search or enter a target role": "Cari atau taip peranan sasaran",
    "See the exact report screen": "Lihat skrin laporan sebenar",
    "See the kind of feedback you will get": "Lihat contoh maklum balas yang akan diterima",
    "See where your role fits.": "Lihat padanan peranan anda.",
    "Select a point to follow the process.": "Pilih titik untuk mengikuti proses.",
    "Select a target role or let the system suggest one. The report shows the role you chose, its database mapping, and the strongest matching roles.": "Pilih peranan sasaran atau biarkan sistem mencadangkannya. Laporan memaparkan peranan dipilih, padanan pangkalan data dan peranan yang paling sepadan.",
    "Show the result of the cloud project": "Tunjukkan hasil projek awan",
    "Sign in to continue with your resume analysis.": "Log masuk untuk meneruskan analisis resume anda.",
    "Sign in to securely analyze and save your resume. PDF, PNG, JPG, and JPEG are supported.": "Log masuk untuk menganalisis dan menyimpan resume anda dengan selamat. PDF, PNG, JPG dan JPEG disokong.",
    "Soft Skills": "Kemahiran insaniah",
    "Strength is an estimate. Avoid names and common passwords.": "Kekuatan ialah anggaran. Elakkan nama dan kata laluan lazim.",
    "Submission details": "Butiran penyerahan",
    "Target job role": "Peranan kerja sasaran",
    "Target role pending.": "Peranan sasaran belum tersedia.",
    "Tap one to fill the role": "Ketik satu untuk memilih peranan",
    "Technical Fit": "Padanan teknikal",
    "Terms of Service": "Syarat Perkhidmatan",
    "Terms of service": "Syarat perkhidmatan",
    "Text-matching notes will appear after analysis.": "Nota padanan teks akan dipaparkan selepas analisis.",
    "The generated resume will use the recommended role, matched evidence, and missing keyword targets.": "Resume yang dijana akan menggunakan peranan dicadangkan, bukti sepadan dan sasaran kata kunci yang kurang.",
    "These are evidence-match scores from resume text and database requirements, not hiring probabilities or validated model accuracy.": "Ini ialah skor padanan bukti daripada teks resume dan keperluan pangkalan data, bukan kebarangkalian diterima kerja atau ketepatan model yang disahkan.",
    "These are the signals the system will preserve when generating the ATS resume draft.": "Bukti inilah yang akan dikekalkan oleh sistem ketika menjana draf resume ATS.",
    "This example shows how feedback appears. Its score and recommendations are not based on your resume.": "Contoh ini menunjukkan rupa maklum balas. Skor dan cadangannya tidak berdasarkan resume anda.",
    "This is fictional sample data, never an analysis of your resume.": "Ini data contoh rekaan, bukan analisis resume anda.",
    "This preview uses fictional sample data.": "Pratonton ini menggunakan data contoh rekaan.",
    "Tools, languages, and role keywords.": "Alat, bahasa pengaturcaraan dan kata kunci peranan.",
    "Top 3 Role Matches": "3 padanan peranan teratas",
    "Top Database Matches": "Padanan pangkalan data teratas",
    "Top role matches will appear after analysis.": "Padanan peranan teratas akan dipaparkan selepas analisis.",
    "Type a role or choose a popular market role. You can also skip this and let the system infer from your resume.": "Taip peranan atau pilih peranan popular. Anda juga boleh melangkau langkah ini dan membiarkan sistem mencadangkannya daripada resume.",
    "Understand the evidence.": "Fahami bukti.",
    "Understand your result": "Fahami keputusan anda",
    "Uploaded resumes are processed to generate ATS feedback, keyword matching, and improvement suggestions.": "Resume yang dimuat naik diproses untuk menghasilkan maklum balas ATS, padanan kata kunci dan cadangan penambahbaikan.",
    "Users are responsible for the accuracy of uploaded resumes and selected target roles.": "Pengguna bertanggungjawab terhadap ketepatan resume yang dimuat naik dan peranan sasaran yang dipilih.",
    "View full report": "Lihat laporan penuh",
    "Waiting for analysis": "Menunggu analisis",
    "We use account details and resume content to provide this educational tool.": "Kami menggunakan butiran akaun dan kandungan resume untuk menyediakan alat pendidikan ini.",
    "We will send a PIN to your new address before switching your account email.": "Kami akan menghantar PIN ke alamat baharu sebelum menukar e-mel akaun anda.",
    "What the analysis found": "Apa yang ditemui oleh analisis",
    "Why This Role": "Mengapa peranan ini",
    "Work Style": "Gaya kerja",
    "Write only what you actually did. Use tools, actions, and outcomes you can support.": "Tulis hanya perkara yang benar-benar anda lakukan. Gunakan alat, tindakan dan hasil yang dapat dibuktikan.",
    "Write with evidence": "Tulis dengan bukti",
    "Your files and next steps": "Fail dan langkah seterusnya",
    "Your matched resume signals will appear here.": "Bukti resume anda yang sepadan akan dipaparkan di sini.",
    "Your next draft starts here": "Draf seterusnya bermula di sini",
    "Your next three edits": "Tiga suntingan seterusnya",
    "Your resume, clearly reviewed.": "Resume anda, disemak dengan jelas.",
    "Your role comparison and resume evidence appear below.": "Perbandingan peranan dan bukti resume anda dipaparkan di bawah.",
    "A clearer view of": "Gambaran lebih jelas tentang",
    "insights.": "wawasan.",
    "what is happening.": "apa yang berlaku.",
    "Actions": "Tindakan",
    "Admin account": "Akaun pentadbir",
    "Admin workspace": "Ruang kerja pentadbir",
    "After scores estimate potential gains from suggested changes. They do not measure a revised resume.": "Skor selepas menganggarkan potensi peningkatan daripada cadangan. Skor ini tidak mengukur resume yang telah disemak.",
    "Before": "Sebelum",
    "Checking access": "Menyemak akses",
    "Date": "Tarikh",
    "Edit User": "Sunting pengguna",
    "Est. After": "Anggaran selepas",
    "Estimated resume improvement": "Anggaran peningkatan resume",
    "Evaluation Score Bands": "Julat skor penilaian",
    "Evaluations": "Penilaian",
    "Family": "Kumpulan",
    "Gain": "Peningkatan",
    "Last Evaluation": "Penilaian terakhir",
    "Latest Score": "Skor terkini",
    "Live activity": "Aktiviti semasa",
    "Name": "Nama",
    "Preparing admin insights": "Menyediakan wawasan pentadbir",
    "Recent Resume Evaluations": "Penilaian resume terkini",
    "Registered Users": "Pengguna berdaftar",
    "Requirement Types": "Jenis keperluan",
    "Requirements": "Keperluan",
    "Review account activity, resume evaluations, and the job-role dataset.": "Semak aktiviti akaun, penilaian resume dan set data peranan kerja.",
    "Role Families": "Kumpulan peranan",
    "Roles With Most Requirements": "Peranan dengan keperluan terbanyak",
    "Save User": "Simpan pengguna",
    "System": "Sistem",
    "These totals come from registered accounts and recorded resume activity. Open the sections below for the detailed data.": "Jumlah ini datang daripada akaun berdaftar dan aktiviti resume yang direkodkan. Buka bahagian di bawah untuk data terperinci.",
    "This dashboard is available to signed-in administrators.": "Papan pemuka ini tersedia kepada pentadbir yang telah log masuk.",
    "Top Evaluated Roles": "Peranan paling kerap dinilai",
    "Top Skill Keywords": "Kata kunci kemahiran teratas",
    "Top Skills": "Kemahiran teratas",
    "Top Text Keywords": "Kata kunci teks teratas",
    "Try again": "Cuba lagi",
    "User": "Pengguna",
    "User Account": "Akaun pengguna",
    "Unable to load history.": "Tidak dapat memuatkan sejarah.",
    "Could not save your name.": "Tidak dapat menyimpan nama anda.",
    "Name updated.": "Nama telah dikemas kini.",
    "Could not upload your photo.": "Tidak dapat memuat naik foto anda.",
    "Photo updated.": "Foto telah dikemas kini.",
    "Could not remove your photo.": "Tidak dapat membuang foto anda.",
    "Photo removed.": "Foto telah dibuang.",
    "Could not change your email.": "Tidak dapat menukar e-mel anda.",
    "Email updated and verified.": "E-mel telah dikemas kini dan disahkan.",
    "No saved evaluations yet.": "Belum ada penilaian tersimpan.",
    "No past evaluations found.": "Tiada penilaian terdahulu ditemui.",
    "View Sample Evaluation": "Lihat contoh penilaian",
    "View": "Lihat",
    "Score": "Skor",
    "Open a sample evaluated resume to see the score, feedback, recommendation, and generated resume flow.": "Buka contoh resume yang dinilai untuk melihat skor, maklum balas, cadangan dan aliran penjanaan resume.",
    "Open a sample evaluated resume to explore the complete analysis flow.": "Buka contoh resume yang dinilai untuk meneroka keseluruhan aliran analisis.",
    "Open report": "Buka laporan",
    "Delete report": "Padam laporan",
    "Could not delete this report.": "Tidak dapat memadam laporan ini.",
    "Could not clear history.": "Tidak dapat memadam sejarah.",
    "History cleared.": "Sejarah telah dipadam.",
    "Loading history...": "Memuatkan sejarah...",
    "Loading saved reports...": "Memuatkan laporan tersimpan...",
    "Could not load saved reports. Showing reports on this device.": "Tidak dapat memuatkan laporan tersimpan. Memaparkan laporan pada peranti ini.",
    "Only on this device": "Hanya pada peranti ini",
    "Could not open this saved report.": "Tidak dapat membuka laporan tersimpan ini.",
    "Clear all saved reports from this account and device?": "Padam semua laporan tersimpan daripada akaun dan peranti ini?",
    "Could not clear saved reports.": "Tidak dapat memadam laporan tersimpan.",
    "Delete this report from your account?": "Padam laporan ini daripada akaun anda?",
    "(optional)": "(pilihan)",
    "Analyze resume": "Analisis resume",
    "Before · vague": "Sebelum · kabur",
    "After · specific": "Selepas · khusus",
    "Feedback with a": "Maklum balas dengan",
    "Find the story behind": "Fahami cerita di sebalik",
    "clear purpose.": "tujuan yang jelas.",
    "your score.": "skor anda.",
    "If the student measured deployment time, reliability, or usage, add the verified number to the project bullet.": "Jika pelajar mengukur masa pelaksanaan, kebolehpercayaan atau penggunaan, tambahkan angka yang disahkan pada poin projek.",
    "It offers guidance for resume preparation. It is not an official university service or an employer's hiring system.": "Ia memberi panduan penyediaan resume. Ini bukan perkhidmatan rasmi universiti atau sistem pengambilan pekerja.",
    "Keep the most relevant project bullets near the top and explain how they demonstrate programming work.": "Letakkan poin projek yang paling relevan di bahagian atas dan terangkan cara poin itu membuktikan kerja pengaturcaraan.",
    "Know what to edit next.": "Ketahui perkara yang perlu disunting seterusnya.",
    "Name the feature or endpoint the student built, the tools used, and the test performed, if those details are true.": "Nyatakan ciri atau endpoint yang dibina, alat yang digunakan dan ujian yang dijalankan jika butirannya benar.",
    "Review evidence and improvements in your report.": "Semak bukti dan cadangan dalam laporan anda.",
    "Score components and matched resume signals make the result easier to read. You can see which keywords, skills, and experience support the match.": "Komponen skor dan bukti resume yang sepadan memudahkan keputusan difahami. Anda boleh melihat kata kunci, kemahiran dan pengalaman yang menyokong padanan.",
    "Scores describe evidence in the resume. They do not predict hiring decisions.": "Skor menggambarkan bukti dalam resume. Skor tidak meramalkan keputusan pengambilan pekerja.",
    "Source:": "Sumber:",
    "Strong": "Kuat",
    "Reset PIN": "PIN tetapan semula",
    "The app uses PDF extraction, optional image OCR, TF-IDF similarity scoring, and simple rule-based feedback generation.": "Aplikasi ini menggunakan pengekstrakan PDF, OCR imej jika perlu, pemarkahan keserupaan TF-IDF dan penjanaan maklum balas berasaskan peraturan mudah.",
    "The app uses PDF text extraction, optional image OCR, TF-IDF similarity scoring, and rule-based feedback generation.": "Aplikasi ini menggunakan pengekstrakan teks PDF, OCR imej jika perlu, pemarkahan keserupaan TF-IDF dan penjanaan maklum balas berasaskan peraturan.",
    "the built-in fictional sample report": "contoh laporan rekaan terbina dalam",
    "The goal is clear, truthful detail a reader can understand. This fictional example shows how specificity helps.": "Matlamatnya ialah butiran yang jelas, benar dan mudah difahami. Contoh rekaan ini menunjukkan kepentingan penerangan khusus.",
    "The layout and workflows were shaped to support resume preparation for UTS Computer Science students.": "Susun atur dan aliran kerja direka untuk menyokong penyediaan resume pelajar Sains Komputer UTS.",
    "The report turns gaps into priority actions, then checks resume readability and writing clarity. Use each suggestion only when it reflects your real work.": "Laporan menukar jurang kepada tindakan utama, kemudian menyemak kebolehbacaan resume dan kejelasan penulisan. Gunakan cadangan hanya jika ia mencerminkan kerja sebenar anda.",
    "These panels use the built-in fictional sample's role comparison, score evidence, and next edits. Open the linked report to see the exact screens.": "Panel ini menggunakan contoh rekaan terbina dalam untuk perbandingan peranan, bukti skor dan suntingan seterusnya. Buka laporan berkaitan untuk melihat skrin sebenar.",
    "This educational project was developed at the University of Technology Sarawak (UTS), Malaysia.": "Projek pendidikan ini dibangunkan di University of Technology Sarawak (UTS), Malaysia.",
    "This preview uses the same fictional sample and section names as the results page. Your own report will use your uploaded resume and the role you choose or the one the system suggests.": "Pratonton ini menggunakan contoh rekaan dan nama bahagian yang sama seperti halaman keputusan. Laporan anda sendiri akan menggunakan resume yang dimuat naik dan peranan yang anda pilih atau dicadangkan oleh sistem.",
    "This tool provides resume feedback for UTS Computer Science students and is intended as guidance only.": "Alat ini memberi maklum balas resume kepada pelajar Sains Komputer UTS dan bertujuan sebagai panduan sahaja.",
    "Turn resume feedback into a clearer next step.": "Tukar maklum balas resume kepada langkah seterusnya yang lebih jelas.",
    "Your account is required to analyze and save a resume. Only upload information you have permission to share.": "Akaun diperlukan untuk menganalisis dan menyimpan resume. Muat naik hanya maklumat yang anda mempunyai kebenaran untuk berkongsi.",
    "Your account stores your name, email, and a password hash. Saved analysis records can include text extracted from your resume.": "Akaun anda menyimpan nama, e-mel dan hash kata laluan. Rekod analisis yang disimpan boleh mengandungi teks yang diekstrak daripada resume anda.",
    "Your skills and projects matter differently for each role. The navigator reads the evidence in your resume, compares it with a target role, and shows what deserves attention.": "Kemahiran dan projek anda mempunyai kepentingan berbeza mengikut peranan. Navigator membaca bukti dalam resume, membandingkannya dengan peranan sasaran dan menunjukkan perkara yang perlu diberi perhatian.",
    "Built for student resume learning": "Dibina untuk pembelajaran resume pelajar",
    "CV Name": "Nama CV",
    "Education markers: 0": "Penanda pendidikan: 0",
    "Experience markers: 0": "Penanda pengalaman: 0",
    "Missing keywords: 0": "Kata kunci yang kurang: 0",
    "Pending": "Belum tersedia",
    "Ready after analysis.": "Tersedia selepas analisis.",
    "Risk: -": "Risiko: -",
    "Skills found: 0": "Kemahiran ditemui: 0",
    "Submitted": "Dihantar",
    "This complete target-role reference is not the user's resume and was not generated from the user's experience.": "Rujukan penuh peranan sasaran ini bukan resume pengguna dan tidak dijana daripada pengalaman pengguna.",
    "This student project was developed for University of Technology Sarawak (UTS), Malaysia. It provides educational resume feedback and does not represent an official university recruitment service.": "Projek pelajar ini dibangunkan untuk University of Technology Sarawak (UTS), Malaysia. Ia memberi maklum balas resume untuk pendidikan dan bukan perkhidmatan pengambilan pekerja rasmi universiti.",
    "EMASCO/MASCO Dataset Overview": "Gambaran keseluruhan set data EMASCO/MASCO",
    "Fair": "Sederhana",
    "Needs work": "Perlu diperbaiki",
    "Low readiness": "Kesediaan rendah",
    "Needs improvement": "Perlu penambahbaikan",
    "Good but not complete": "Baik tetapi belum lengkap",
    "Strong readiness": "Kesediaan kukuh",
    "Below 30 means the CV is unlikely to pass ATS screening for the compared role yet.": "Skor bawah 30 bermakna CV mungkin belum melepasi saringan ATS untuk peranan yang dibandingkan.",
    "30-49 means the CV has some useful evidence, but role keywords, proof, or format need stronger work.": "Skor 30-49 bermakna CV mempunyai beberapa bukti berguna, tetapi kata kunci peranan, bukti atau format perlu diperbaiki.",
    "50-79 means the CV is usable, but it is not fully optimized for the compared role yet.": "Skor 50-79 bermakna CV boleh digunakan, tetapi belum dioptimumkan sepenuhnya untuk peranan yang dibandingkan.",
    "80+ means the CV has strong role and ATS alignment, with small improvements still possible.": "Skor 80+ bermakna CV mempunyai padanan peranan dan ATS yang kuat, dengan sedikit ruang penambahbaikan.",
    "not available": "tidak tersedia",
    "No suitable CS role was confirmed. The closest evidence requires human review.": "Tiada peranan CS yang sesuai disahkan. Bukti terdekat memerlukan semakan manusia.",
    "Treat the result as NOT MATCH. Use the selected-field fictional example only as a learning guide and add only evidence you genuinely have.": "Anggap keputusan sebagai TIDAK SEPADAN. Gunakan contoh rekaan bagi bidang dipilih sebagai panduan pembelajaran dan tambahkan hanya bukti sebenar anda.",
    "Review the closest role cards manually. The fallback resume is a fictional learning example, not a job recommendation.": "Semak kad peranan terdekat secara manual. Resume gantian ialah contoh pembelajaran rekaan, bukan cadangan kerja.",
    "Notes use the Top 1 detected role because no job was selected.": "Nota menggunakan peranan teratas yang dikesan kerana tiada pekerjaan dipilih.",
    "Next edit: quantify impact and keep the ATS format clean.": "Suntingan seterusnya: ukur impak dan kekalkan format ATS yang kemas.",
    "No Suitable CS Role Confirmed": "Tiada peranan CS yang sesuai disahkan",
    "REVIEW REQUIRED · NOT MATCH": "SEMAKAN DIPERLUKAN · TIDAK SEPADAN",
    "REVIEW REQUIRED": "SEMAKAN DIPERLUKAN",
    "Top 3 Resume-Matched Roles": "3 peranan teratas yang sepadan dengan resume",
    "Top 3 Closest Evidence Comparisons": "3 perbandingan bukti terdekat",
    "Top 1 Detected Role": "Peranan teratas yang dikesan",
    "Evidence comparisons": "Perbandingan bukti",
    "Not recommendations": "Bukan cadangan",
    "Auto-detected role": "Peranan dikesan secara automatik",
    "Auto preview": "Pratonton automatik",
    "Close role evidence": "Bukti peranan hampir sepadan",
    "Better database fit found": "Padanan pangkalan data yang lebih baik ditemui",
    "Selected role confirmed": "Peranan dipilih disahkan",
    "No selected database role was used.": "Tiada peranan pangkalan data dipilih digunakan.",
    "Used for the final readiness score.": "Digunakan untuk skor kesediaan akhir.",
    "Selected role requires review and is recorded as NOT MATCH.": "Peranan dipilih memerlukan semakan dan direkod sebagai TIDAK SEPADAN.",
    "No job was selected; no suitable role was confirmed.": "Tiada pekerjaan dipilih; tiada peranan sesuai disahkan.",
    "Human review required before choosing a target role.": "Semakan manusia diperlukan sebelum memilih peranan sasaran.",
    "You selected this role. The Top 3 list below shows why another database role matched stronger.": "Anda memilih peranan ini. Senarai 3 teratas di bawah menunjukkan mengapa peranan pangkalan data lain lebih sepadan.",
    "Used for the final ATS score because the resume evidence matches this database role more strongly.": "Digunakan untuk skor ATS akhir kerana bukti resume lebih sepadan dengan peranan pangkalan data ini.",
    "Final comparison role used in this result.": "Peranan perbandingan akhir yang digunakan dalam keputusan ini.",
    "Selected role confirmed as the database match. The Top 3 list below shows nearby alternatives.": "Peranan dipilih disahkan sebagai padanan pangkalan data. Senarai 3 teratas di bawah menunjukkan alternatif hampir sepadan.",
    "Custom role typed by user. The Top 3 list below shows the closest database roles.": "Peranan tersuai ditaip oleh pengguna. Senarai 3 teratas di bawah menunjukkan peranan pangkalan data terdekat.",
    "Used for the final ATS score because it matches the selected role and resume evidence.": "Digunakan untuk skor ATS akhir kerana sepadan dengan peranan dipilih dan bukti resume.",
    "Ready to generate from extracted resume text.": "Sedia menjana daripada teks resume yang diekstrak.",
    "Ready to generate from available analysis context.": "Sedia menjana daripada konteks analisis yang tersedia.",
    "Analyze your own resume to create a draft": "Analisis resume anda sendiri untuk mencipta draf",
    "Resume drafting is available after you analyze your own resume. This report uses fictional sample data.": "Penjanaan draf resume tersedia selepas anda menganalisis resume sendiri. Laporan ini menggunakan data contoh rekaan.",
    "No major missing keyword targets were found for this role.": "Tiada sasaran kata kunci penting yang kurang ditemui untuk peranan ini.",
    "No matched resume signals detected yet.": "Belum ada bukti resume sepadan yang dikesan.",
    "No ATS quality criteria were returned for this analysis.": "Tiada kriteria kualiti ATS dikembalikan untuk analisis ini.",
    "No major ATS quality issues detected.": "Tiada isu besar kualiti ATS dikesan.",
    "No passed checks yet": "Belum ada semakan yang lulus",
    "No major marked problems detected.": "Tiada masalah besar yang ditanda dikesan.",
    "Issues to fix": "Isu yang perlu dibaiki",
    "Issue explanations": "Penjelasan isu",
    "Passed checks": "Semakan lulus"
  };
  const dynamicTranslations = [
    [/^Open account for (.+)$/, (match) => `Buka akaun untuk ${match[1]}`],
    [/^Open profile for (.+)$/, (match) => `Buka profil untuk ${match[1]}`],
    [/^Signed in as (.+)\. You can now check your resume\.$/, (match) => `Log masuk sebagai ${match[1]}. Anda kini boleh menyemak resume.`],
    [/^Compared with (.+)\. Review the matched evidence and start with the suggested edits below\.$/, (match) => `Dibandingkan dengan ${match[1]}. Semak bukti yang sepadan dan mulakan dengan suntingan yang dicadangkan.`],
    [/^The evidence for (.+) needs human review\. Use the findings below as guidance for your next edit\.$/, (match) => `Bukti untuk ${match[1]} memerlukan semakan manusia. Gunakan dapatan di bawah sebagai panduan untuk suntingan seterusnya.`],
    [/^Top detected role: (.+)\.$/, (match) => `Peranan teratas dikesan: ${match[1]}.`],
    [/^Compared role: (.+)\.$/, (match) => `Peranan perbandingan: ${match[1]}.`],
    [/^Stronger match: (.+) over (.+)\.$/, (match) => `Padanan lebih kuat: ${match[1]} berbanding ${match[2]}.`],
    [/^Next edit for (.+): add evidence for (.+)\.$/, (match) => `Suntingan seterusnya untuk ${match[1]}: tambah bukti bagi ${match[2]}.`],
    [/^Recommended: (.+) over (.+)$/, (match) => `Disyorkan: ${match[1]} berbanding ${match[2]}`],
    [/^Recommended: (.+)$/, (match) => `Disyorkan: ${match[1]}`],
    [/^Top Detected Role: (.+)$/, (match) => `Peranan teratas dikesan: ${match[1]}`],
    [/^Review Required: Not a Match for (.+)$/, (match) => `Semakan diperlukan: Tidak sepadan dengan ${match[1]}`],
    [/^Database role: (.+)$/, (match) => `Peranan pangkalan data: ${match[1]}`],
    [/^Closest resume-detected role: (.+)$/, (match) => `Peranan terdekat dikesan daripada resume: ${match[1]}`],
    [/^Skills found: (\d+)$/, (match) => `Kemahiran ditemui: ${match[1]}`],
    [/^Education markers: (\d+)$/, (match) => `Penanda pendidikan: ${match[1]}`],
    [/^Experience markers: (\d+)$/, (match) => `Penanda pengalaman: ${match[1]}`],
    [/^Important keywords: (\d+)$/, (match) => `Kata kunci penting: ${match[1]}`],
    [/^Missing keywords: (\d+)$/, (match) => `Kata kunci yang kurang: ${match[1]}`],
    [/^Target: (.+)$/, (match) => `Sasaran: ${match[1]}`],
    [/^File: (.+)$/, (match) => `Fail: ${match[1]}`],
    [/^Size: (.+)$/, (match) => `Saiz: ${match[1]}`],
    [/^Type: (.+)$/, (match) => `Jenis: ${match[1]}`]
  ];
  const translatedText = new WeakMap();
  const translatedAttributes = new WeakMap();
  let language = "en";
  try { language = localStorage.getItem(LANGUAGE_KEY) === "ms" ? "ms" : "en"; } catch (error) { /* Storage can be unavailable. */ }

  function translateEnglishToMalay(englishText) {
    if (Object.prototype.hasOwnProperty.call(translations, englishText)) return translations[englishText];
    for (const [pattern, build] of dynamicTranslations) {
      const match = englishText.match(pattern);
      if (match) return build(match);
    }
    return null;
  }

  function translateUiText(englishText) {
    return language === "ms" ? (translateEnglishToMalay(englishText) || englishText) : englishText;
  }

  function translateNode(node) {
    if (!node) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (!parent || parent.closest("script,style,noscript,textarea,option,[data-no-translate]")) return;
      const raw = node.nodeValue;
      let original = translatedText.get(node);
      if (original && raw !== original && raw.trim() !== translateEnglishToMalay(original.trim())) original = null;
      if (!original) {
        original = raw;
        if (!translateEnglishToMalay(original.trim())) return;
        translatedText.set(node, original);
      }
      const desired = language === "ms"
        ? original.replace(original.trim(), translateEnglishToMalay(original.trim()))
        : original;
      if (raw !== desired) node.nodeValue = desired;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.matches("script,style,noscript,textarea,[data-no-translate]")) return;
    ["placeholder", "title", "aria-label"].forEach((attribute) => {
      const raw = node.getAttribute(attribute);
      if (!raw) return;
      let originals = translatedAttributes.get(node);
      let original = originals && originals[attribute];
      if (original && raw !== original && raw !== translateEnglishToMalay(original)) original = null;
      if (!original) {
        original = raw;
        if (!translateEnglishToMalay(original)) return;
        if (!originals) { originals = {}; translatedAttributes.set(node, originals); }
        originals[attribute] = original;
      }
      const desired = language === "ms" ? translateEnglishToMalay(original) : original;
      if (raw !== desired) node.setAttribute(attribute, desired);
    });
    for (const child of node.childNodes) translateNode(child);
  }

  function setLanguage(nextLanguage) {
    language = nextLanguage === "ms" ? "ms" : "en";
    try { localStorage.setItem(LANGUAGE_KEY, language); } catch (error) { /* Keep this page usable. */ }
    document.documentElement.lang = language;
    document.querySelectorAll("[data-language-option]").forEach((button) => {
      const active = button.dataset.languageOption === language;
      button.setAttribute("aria-pressed", String(active));
      button.classList.toggle("is-active", active);
    });
    translateNode(document.body);
  }

  function initializeHeaderAccountControls() {
    const trigger = document.getElementById("openAuthModalBtn");
    if (!trigger) return;
    if (!document.querySelector("[data-header-account-name]")) {
      const name = document.createElement("span");
      name.className = "account-header-name";
      name.dataset.headerAccountName = "";
      name.hidden = true;
      trigger.before(name);
    }
    if (!document.querySelector("[data-language-switch]")) {
      const switcher = document.createElement("div");
      switcher.className = "account-language-switch";
      switcher.dataset.languageSwitch = "";
      switcher.setAttribute("role", "group");
      switcher.setAttribute("aria-label", "Language / Bahasa");
      [["en", "English", "EN"], ["ms", "Bahasa Melayu", "BM"]].forEach(([code, label, shortLabel]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.languageOption = code;
        button.setAttribute("aria-label", label);
        button.title = label;
        const full = document.createElement("span");
        full.className = "account-language-full";
        full.textContent = label;
        const short = document.createElement("span");
        short.className = "account-language-short";
        short.textContent = shortLabel;
        button.append(full, short);
        button.addEventListener("click", () => setLanguage(code));
        switcher.appendChild(button);
      });
      const name = document.querySelector("[data-header-account-name]");
      (name || trigger).before(switcher);
      setLanguage(language);
    }
  }

  function updateHeaderAccount(user, photoUrl) {
    initializeHeaderAccountControls();
    const trigger = document.getElementById("openAuthModalBtn");
    if (!trigger) return;
    const name = document.querySelector("[data-header-account-name]");
    const icon = trigger.querySelector(".account-header-trigger-icon");
    if (!icon) return;
    if (!trigger._guestIcon) trigger._guestIcon = icon.cloneNode(true);
    const signedIn = Boolean(user);
    trigger.classList.toggle("is-signed-in", signedIn);
    if (name) {
      name.textContent = signedIn ? String(user.full_name || user.email || "Your account") : "";
      name.hidden = !signedIn;
    }
    if (!signedIn) {
      icon.replaceChildren(...Array.from(trigger._guestIcon.childNodes).map((child) => child.cloneNode(true)));
      icon.classList.remove("has-avatar", "has-initials");
      return;
    }
    if (photoUrl) {
      const image = document.createElement("img");
      image.src = photoUrl;
      image.alt = "";
      icon.replaceChildren(image);
      icon.classList.add("has-avatar");
      icon.classList.remove("has-initials");
    } else {
      const initials = String(user.full_name || user.email || "U").trim().split(/\s+/).slice(0, 2)
        .map((part) => part.charAt(0)).join("").toUpperCase() || "U";
      icon.textContent = initials;
      icon.classList.add("has-initials");
      icon.classList.remove("has-avatar");
    }
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
    if (!isValidEmail(cleanEmail)) {
      throw new Error("Enter a valid email address first.");
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
    if (elements.passwordInput) {
      elements.passwordInput.value = "";
      elements.passwordInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
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
      updateHeaderAccount(currentUser);
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
        updateHeaderAccount(currentUser, avatarObjectUrl);
        if (removeAvatarButton) removeAvatarButton.hidden = false;
      } catch (error) {
        // Initials remain visible when there is no photo or the connection fails.
      }
    }

    function clearSensitiveFields() {
      if (newEmailInput) newEmailInput.value = "";
      if (emailPasswordInput) emailPasswordInput.value = "";
      if (emailPinInput) emailPinInput.value = "";
      updateEmailPinAvailability();
      showMessage(emailMessage, "");
      showMessage(nameMessage, "");
      showMessage(avatarMessage, "");
    }

    let emailPinSending = false;
    function updateEmailPinAvailability() {
      if (!sendEmailPinButton) return;
      const newEmail = newEmailInput?.value.trim().toLowerCase() || "";
      const currentEmail = String(currentUser?.email || "").toLowerCase();
      sendEmailPinButton.disabled = emailPinSending || !currentUser || Boolean(currentUser.is_admin) ||
        !isValidEmail(newEmail) || !newEmailInput.checkValidity() || newEmail === currentEmail || !emailPasswordInput?.value.trim();
    }

    function show(view) {
      const allowedViews = hasAccount ? ["info", "settings", "history"] : ["access"];
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
      updateHeaderAccount(currentUser, String(currentUser?.email || "") === avatarEmail ? avatarObjectUrl : null);
      updateEmailPinAvailability();
      const name = user ? String(user.full_name || "Your account") : "Guest";
      const email = user ? String(user.email || "") : "Sign in or create an account";
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
      if (!hasAccount) show("access");
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
    if (newEmailInput) newEmailInput.addEventListener("input", () => {
      if (emailPinInput) emailPinInput.value = "";
      updateEmailPinAvailability();
    });
    if (emailPasswordInput) emailPasswordInput.addEventListener("input", updateEmailPinAvailability);
    updateEmailPinAvailability();
    if (sendEmailPinButton) sendEmailPinButton.addEventListener("click", async () => {
      if (emailPinSending || !currentUser || currentUser.is_admin) return;
      const newEmail = newEmailInput.value.trim().toLowerCase();
      const currentPassword = emailPasswordInput.value;
      if (!isValidEmail(newEmail) || !newEmailInput.checkValidity()) return showMessage(emailMessage, "Enter a valid new email address.", true);
      if (newEmail === String(currentUser.email || "").toLowerCase()) return showMessage(emailMessage, "Enter a different email address.", true);
      if (!currentPassword.trim()) return showMessage(emailMessage, "Enter your current password first.", true);
      emailPinSending = true;
      updateEmailPinAvailability();
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
        emailPinSending = false;
        updateEmailPinAvailability();
      }
    });

    if (emailForm) emailForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!currentUser) return;
      const newEmail = newEmailInput.value.trim().toLowerCase();
      const currentPassword = emailPasswordInput.value;
      const verificationPin = emailPinInput.value.trim();
      if (!isValidEmail(newEmail) || !newEmailInput.checkValidity()) return showMessage(emailMessage, "Enter a valid new email address.", true);
      if (newEmail === String(currentUser.email || "").toLowerCase()) return showMessage(emailMessage, "Enter a different email address.", true);
      if (!currentPassword.trim()) return showMessage(emailMessage, "Enter your current password first.", true);
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
        updateEmailPinAvailability();
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
    const apiBaseUrl = options.apiBaseUrl || (window.location.protocol.startsWith("http") ? window.location.origin : "http://127.0.0.1:5000");
    let focusBeforeOpen = null;
    let renderVersion = 0;
    let serverAvailable = false;

    function showHistoryMessage(message, isError = false) {
      if (!list) return;
      const notice = document.createElement("p");
      notice.className = `mb-3 rounded-lg border px-3 py-2 text-sm ${isError ? "border-error/30 bg-error-container/40 text-error" : "border-outline-variant/60 bg-surface-container-lowest text-on-surface-variant"}`;
      notice.setAttribute("role", isError ? "alert" : "status");
      notice.textContent = translateUiText(message);
      list.prepend(notice);
    }

    function savedAnalysisId(item) {
      const id = Number(item && (item.analysis_id || (item.full && item.full.analysis_id)));
      return Number.isSafeInteger(id) && id > 0 ? id : null;
    }

    async function requestHistory(path, method = "GET") {
      const response = await fetch(apiBaseUrl + path, {
        method,
        credentials: "include",
        cache: "no-store"
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Unable to load history.");
      return payload;
    }

    function removeLocalHistoryItem(item) {
      const id = savedAnalysisId(item);
      saveHistory(loadHistory().filter((candidate) => id
        ? savedAnalysisId(candidate) !== id
        : candidate.history_key !== item.history_key && candidate.id !== item.id));
    }

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

    function displayItems(items, warning = "") {
      if (!list) {
        return;
      }
      list.innerHTML = "";
      if (options.clearButton) options.clearButton.disabled = items.length === 0;
      if (warning) showHistoryMessage(warning, true);

      if (!items.length) {
        const emptyState = document.createElement("div");
        emptyState.className = "p-4 rounded-lg border border-outline-variant/60 bg-surface-container-lowest";
        emptyState.innerHTML = `
          <div class="font-semibold text-primary">No past evaluations found.</div>
          <p class="mt-1 text-sm text-on-surface-variant">${escapeHtml(translateUiText(options.emptyDescription || "Open a sample evaluated resume to explore the complete analysis flow."))}</p>
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
        element.className = "account-history-item p-3 rounded-lg border border-outline-variant/60 flex items-center justify-between";
        const storedDate = new Date(item.stored_at);
        const dateLabel = Number.isNaN(storedDate.getTime()) ? "" : storedDate.toLocaleString(language === "ms" ? "ms-MY" : "en-MY");
        element.innerHTML = `<div>
          <div class="font-semibold">${escapeHtml(item.uploaded_resume_name)} - ${escapeHtml(item.role || "Role")}</div>
          <div class="text-xs text-on-surface-variant">${escapeHtml(translateUiText("Score"))}: ${Number(item.matching_percentage || 0).toFixed(0)}/100${dateLabel ? ` | ${escapeHtml(dateLabel)}` : ""}</div>
        </div>
        <div class="flex gap-2">
          <button class="viewHistoryBtn px-3 py-1 rounded-lg bg-primary text-on-primary text-sm" type="button">View</button>
          <button class="deleteHistoryBtn px-3 py-1 rounded-lg bg-surface-container-low text-primary text-sm" type="button">Delete</button>
        </div>`;

        element.querySelector(".viewHistoryBtn").addEventListener("click", async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            let selected = item;
            const id = savedAnalysisId(item);
            if (id && serverAvailable) {
              const payload = await requestHistory("/analyses/" + id);
              if (!payload.analysis || typeof payload.analysis !== "object") throw new Error("Could not open this saved report.");
              selected = { ...item, full: payload.analysis };
            } else if (!item.full) {
              throw new Error("Could not open this saved report.");
            }
            if (typeof options.onView === "function") options.onView(selected);
            close();
          } catch (error) {
            showHistoryMessage("Could not open this saved report.", true);
            button.disabled = false;
          }
        });
        element.querySelector(".deleteHistoryBtn").addEventListener("click", async (event) => {
          const id = savedAnalysisId(item);
          if (id && !window.confirm(translateUiText("Delete this report from your account?"))) return;
          const button = event.currentTarget;
          button.disabled = true;
          try {
            if (id) await requestHistory("/analyses/" + id, "DELETE");
            removeLocalHistoryItem(item);
            render();
          } catch (error) {
            showHistoryMessage("Could not delete this report.", true);
            button.disabled = false;
          }
        });
        list.appendChild(element);
      });
    }

    async function render() {
      if (!list) return;
      const version = ++renderVersion;
      const localItems = dedupeHistoryItems(loadHistory());
      saveHistory(localItems.slice(0, 50));
      list.innerHTML = "";
      showHistoryMessage("Loading saved reports...");
      if (options.clearButton) options.clearButton.disabled = true;
      try {
        const payload = await requestHistory("/analyses");
        if (version !== renderVersion) return;
        serverAvailable = true;
        const serverItems = (Array.isArray(payload.analyses) ? payload.analyses : []).map((row) => ({
          id: row.id,
          analysis_id: row.id,
          uploaded_resume_name: row.uploaded_resume_name || "resume.pdf",
          role: row.target_role || "",
          matching_percentage: row.matching_percentage,
          stored_at: row.created_at,
          history_key: "analysis-id:" + row.id,
          is_server: true
        }));
        const unsavedLocalItems = localItems.filter((item) => !savedAnalysisId(item) || item.full?.history_saved === false);
        const items = dedupeHistoryItems([...serverItems, ...unsavedLocalItems])
          .sort((a, b) => (Date.parse(b.stored_at) || 0) - (Date.parse(a.stored_at) || 0));
        displayItems(items);
      } catch (error) {
        if (version !== renderVersion) return;
        serverAvailable = false;
        displayItems(localItems, "Could not load saved reports. Showing reports on this device.");
      }
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
      options.clearButton.addEventListener("click", async () => {
        if (!window.confirm(translateUiText("Clear all saved reports from this account and device?"))) return;
        options.clearButton.disabled = true;
        try {
          await requestHistory("/analyses", "DELETE");
          saveHistory([]);
          render();
        } catch (error) {
          showHistoryMessage("Could not clear saved reports.", true);
          options.clearButton.disabled = false;
        }
      });
    }

    return { open, close, render };
  }

  window.CareerNavigatorCommon = {
    getAuthUser,
    hasCurrentUserReport,
    setAuthUser,
    syncAuthUserFromServer,
    isAuthSessionVerified: () => authSessionVerified,
    getPasswordValidationMessage,
    isValidEmail,
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
    createHistoryModal,
    setLanguage,
    getLanguage: () => language,
    translateUiText
  };

  document.addEventListener("DOMContentLoaded", () => {
    initializeHeaderAccountControls();
    setLanguage(language);
    if (!document.body || typeof MutationObserver === "undefined") return;
    const pending = new Set();
    let scheduled = false;
    const flush = () => {
      scheduled = false;
      if (language !== "ms") { pending.clear(); return; }
      pending.forEach((node) => translateNode(node));
      pending.clear();
    };
    const observer = new MutationObserver((mutations) => {
      if (language !== "ms") return;
      mutations.forEach((mutation) => {
        if (mutation.type === "childList") {
          mutation.addedNodes.forEach((node) => pending.add(node));
        } else {
          pending.add(mutation.target);
        }
      });
      if (!scheduled) {
        scheduled = true;
        queueMicrotask(flush);
      }
    });
    observer.observe(document.body, {
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["placeholder", "title", "aria-label"],
      subtree: true
    });
    window.addEventListener("storage", (event) => {
      if (event.key === LANGUAGE_KEY) setLanguage(event.newValue);
    });
  });

  document.addEventListener("DOMContentLoaded", () => {
    const passwordInput = document.getElementById("loginPasswordInput");
    const emailInput = document.getElementById("loginEmailInput");
    const authMessage = document.getElementById("authMessage");
    const clearStaleLoginError = () => {
      if (!authMessage || !authMessage.classList.contains("text-error")) return;
      authMessage.textContent = "";
      authMessage.classList.add("hidden");
    };
    if (passwordInput) passwordInput.addEventListener("input", clearStaleLoginError);
    if (emailInput) emailInput.addEventListener("input", clearStaleLoginError);
  });

  document.addEventListener("DOMContentLoaded", () => {
    const rules = {
      length: (value) => value.length >= 8,
      letter: (value) => /[A-Za-z]/.test(value),
      number: (value) => /\d/.test(value),
      symbol: (value) => /[^A-Za-z0-9]/.test(value)
    };
    const makeFeedback = (input) => {
      const prefix = input.id;
      const feedback = document.createElement("div");
      feedback.id = `${prefix}Feedback`;
      feedback.className = "account-password-feedback auth-password-feedback";
      feedback.dataset.level = "empty";
      feedback.innerHTML = `<div class="account-password-feedback-head"><span>Password strength</span><strong id="${prefix}StrengthLabel" role="status" aria-live="polite">Start typing</strong></div>
        <div class="account-password-meter" id="${prefix}Meter" role="meter" aria-label="Password strength" aria-valuemin="0" aria-valuemax="5" aria-valuenow="0" aria-valuetext="Start typing"><span></span></div>
        <ul class="account-password-requirements" id="${prefix}Requirements"><li data-password-rule="length">8 or more characters</li><li data-password-rule="letter">A letter</li><li data-password-rule="number">A number</li><li data-password-rule="symbol">A symbol</li></ul>
        <p class="account-password-note">Strength is an estimate. Avoid names and common passwords.</p>`;
      input.insertAdjacentElement("afterend", feedback);
      const describedBy = new Set((input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
      describedBy.add(`${prefix}StrengthLabel`);
      describedBy.add(`${prefix}Requirements`);
      input.setAttribute("aria-describedby", Array.from(describedBy).join(" "));
      return feedback;
    };

    ["profilePasswordInput", "registerPasswordInput", "resetPasswordInput"].forEach((inputId) => {
      const input = document.getElementById(inputId);
      if (!input) return;
      const feedback = document.getElementById(inputId === "profilePasswordInput" ? "profilePasswordFeedback" : `${inputId}Feedback`) || makeFeedback(input);
      const label = feedback.querySelector(".account-password-feedback-head strong");
      const meter = feedback.querySelector(".account-password-meter");
      const meterFill = meter?.querySelector("span");
      if (!label || !meterFill) return;

      const updateStrength = () => {
        const value = input.value;
        let metCount = 0;
        feedback.querySelectorAll("[data-password-rule]").forEach((item) => {
          if (item.dataset.passwordRule === "length") {
            item.textContent = value && value.length < 8 ? `${value.length} of 8 characters` : "8 or more characters";
          }
          const met = Boolean(rules[item.dataset.passwordRule]?.(value));
          if (met) metCount++;
          item.classList.toggle("is-met", met);
          item.setAttribute("aria-label", `${met ? "Met" : "Needed"}: ${item.textContent.trim()}`);
        });
        const valid = !getPasswordValidationMessage(value);
        const score = metCount + (valid && value.length >= 12 ? 1 : 0);
        const level = !value ? "empty" : score <= 2 ? "weak" : score === 3 ? "developing" : score === 4 ? "good" : "strong";
        const strengthText = { empty: "Start typing", weak: "Weak", developing: "Developing", good: "Good", strong: "Strong" }[level];
        feedback.dataset.level = level;
        label.textContent = strengthText;
        meter.setAttribute("aria-valuenow", String(score));
        meter.setAttribute("aria-valuetext", value ? `${strengthText}. ${metCount} of 4 requirements met.` : "Start typing");
        meterFill.style.width = `${score * 20}%`;
        if (value) input.setAttribute("aria-invalid", String(!valid));
        else input.removeAttribute("aria-invalid");
      };
      input.addEventListener("input", updateStrength);
      input.form?.addEventListener("reset", () => setTimeout(updateStrength, 0));
      if (input.form) {
        new MutationObserver(updateStrength).observe(input.form, { attributes: true, attributeFilter: ["class"] });
      }
      updateStrength();
    });
  });

  // Use one visibility control for each password field across account dialogs.
  // Run after password feedback is inserted so its panel stays outside the input wrap.
  document.addEventListener("DOMContentLoaded", () => {
    const eyeIcon = `<svg viewBox="0 0 24 24" aria-hidden="true"><g class="auth-eye-on"><path d="M2.5 12s3.5-5.5 9.5-5.5 9.5 5.5 9.5 5.5-3.5 5.5-9.5 5.5S2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.5"/></g><g class="auth-eye-off"><path d="M3 3 21 21M9.9 6.7A10 10 0 0 1 12 6.5c6 0 9.5 5.5 9.5 5.5a14 14 0 0 1-3.1 3.4M6.2 8.2C3.8 9.8 2.5 12 2.5 12s3.5 5.5 9.5 5.5c1.1 0 2.2-.2 3.1-.5"/></g></svg>`;
    document.querySelectorAll("#authModal input[type='password'], #profileModal input[type='password']").forEach((input) => {
      let wrap = input.closest(".auth-password-wrap");
      if (!wrap) {
        wrap = document.createElement("div");
        wrap.className = "auth-password-wrap";
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
      }
      let button = wrap.querySelector(".auth-password-toggle");
      if (!button) {
        button = document.createElement("button");
        button.className = "auth-password-toggle";
        button.type = "button";
        wrap.appendChild(button);
      }
      button.innerHTML = eyeIcon;
      button.setAttribute("aria-controls", input.id);
      const setVisible = (visible) => {
        input.type = visible ? "text" : "password";
        button.setAttribute("aria-label", visible ? "Hide password" : "Show password");
        button.setAttribute("aria-pressed", String(visible));
      };
      setVisible(false);
      button.addEventListener("click", () => {
        setVisible(input.type === "password");
        input.focus({ preventScroll: true });
      });

      const modal = input.closest("#authModal, #profileModal");
      const form = input.closest("form");
      const panel = input.closest("[data-account-panel]");
      const hideWhenClosed = () => {
        if (modal?.classList.contains("hidden") || form?.classList.contains("hidden") || panel?.hidden) setVisible(false);
      };
      if (modal) new MutationObserver(hideWhenClosed).observe(modal, { attributes: true, attributeFilter: ["class"] });
      if (form) {
        new MutationObserver(hideWhenClosed).observe(form, { attributes: true, attributeFilter: ["class"] });
        form.addEventListener("reset", () => setVisible(false));
      }
      if (panel) new MutationObserver(hideWhenClosed).observe(panel, { attributes: true, attributeFilter: ["hidden"] });
    });
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
