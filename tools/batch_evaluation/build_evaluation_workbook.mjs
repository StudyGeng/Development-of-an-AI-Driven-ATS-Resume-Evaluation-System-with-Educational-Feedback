import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, "..", "..");
const outputDir = path.join(root, "outputs", "batch_resume_evaluation");
const outputPath = path.join(outputDir, "resume_role_batch_evaluation.xlsx");
const previewDir = path.join(outputDir, "previews");
const rows = JSON.parse(await fs.readFile(path.join(outputDir, "evaluation_rows.json"), "utf8"));
const rankings = JSON.parse(await fs.readFile(path.join(outputDir, "all_rankings.json"), "utf8"));
const summaryData = JSON.parse(await fs.readFile(path.join(outputDir, "evaluation_summary.json"), "utf8"));
const roleMetrics = JSON.parse(await fs.readFile(path.join(outputDir, "role_metrics.json"), "utf8"));

const colors = {
  navy: "#0B2E59",
  blue: "#0B66B3",
  blue2: "#3C8DCD",
  paleBlue: "#EAF3FB",
  green: "#1F7A4D",
  paleGreen: "#E8F5EE",
  amber: "#A86100",
  paleAmber: "#FFF3D6",
  red: "#B42318",
  paleRed: "#FDECEA",
  gray900: "#1F2937",
  gray700: "#4B5563",
  gray500: "#6B7280",
  gray300: "#D1D5DB",
  gray200: "#E5E7EB",
  gray100: "#F3F4F6",
  white: "#FFFFFF",
};
const font = "Arial";

function colLetter(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function writeMatrix(sheet, startRow, startCol, matrix) {
  if (!matrix.length || !matrix[0].length) return null;
  const endRow = startRow + matrix.length - 1;
  const endCol = startCol + matrix[0].length - 1;
  const range = sheet.getRange(
    `${colLetter(startCol)}${startRow}:${colLetter(endCol)}${endRow}`,
  );
  range.values = matrix;
  return range;
}

function styleTitle(sheet, lastColumn, title, subtitle) {
  sheet.getRange(`A2:${lastColumn}2`).format.borders = {
    bottom: { style: "medium", color: colors.navy },
  };
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: font, size: 15, bold: true, color: colors.navy };
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange(`A3:${lastColumn}3`).format.font = { name: font, size: 10, italic: true, color: colors.gray700 };
  sheet.getRange(`A3:${lastColumn}3`).format.wrapText = false;
  sheet.getRange(`A2:${lastColumn}3`).format.verticalAlignment = "center";
}

function styleHeader(range) {
  range.format = {
    fill: colors.navy,
    font: { name: font, size: 10, bold: true, color: colors.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: {
      insideVertical: { style: "thin", color: colors.white },
      bottom: { style: "medium", color: colors.navy },
    },
  };
}

function styleBody(range) {
  range.format.font = { name: font, size: 10, color: colors.gray900 };
  range.format.verticalAlignment = "center";
}

function setColumnWidth(sheet, column, width, lastRow) {
  sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = width;
}

function percentOrBlank(value) {
  return value === null || value === undefined ? null : Number(value);
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const allResults = workbook.worksheets.add("All Results");
const reviewQueue = workbook.worksheets.add("Review Queue");
const roleMetricsSheet = workbook.worksheets.add("Role Metrics");
const allRankings = workbook.worksheets.add("All Rankings");
const method = workbook.worksheets.add("Method & Roles");

for (const sheet of [summary, allResults, reviewQueue, roleMetricsSheet, allRankings, method]) {
  sheet.showGridLines = false;
}
summary.tabColor = colors.navy;
reviewQueue.tabColor = "#D97706";
method.tabColor = "#64748B";

// Method and catalog roles are created first because review inputs reference the role list.
styleTitle(
  method,
  "K",
  "Method, definitions and role list",
  "Independent labels are kept separate from system recommendations so the model does not grade itself.",
);
const methodRows = [
  ["Item", "Definition"],
  ["Report date", "2026-10-09 (Asia/Kuala_Lumpur)"],
  ["Corpus", "120 INFORMATION-TECHNOLOGY CSV resumes plus 85 PDFs from Resume_template; 205 total sources."],
  ["Production run", "Every source was ranked against all 95 database roles using backend/app.py. No analysis history or user database records were written."],
  ["Scoring version", (summaryData.role_ranking_versions || ["unknown"]).join(", ")],
  ["Recommendation score", "Evidence-ranking score used to order roles. It is not a hiring probability, ATS readiness score, or calibrated accuracy percentage."],
  ["Score composition", "55% base requirement fit, 10% role-specific evidence and 35% dated employment-title relevance. If no dated title is available, the 35% is redistributed across the 55:10 evidence components."],
  ["Career-title rule", "The current dated title receives full title credit; each older dated title receives 85% of the credit available to the entry before it. Only an exact current-title match can activate an exact-title confidence floor."],
  ["Validated benchmark", "78 synthetic templates with independent mapped acceptable roles derived from their source folders and file content."],
  ["Provisional benchmark", "Real resumes and CSV source-title/family labels. These results are useful diagnostics but are not a final human-reviewed accuracy claim."],
  ["Top-1 / Top-3", "PASS when at least one independently acceptable role appears first or within the first three recommendations."],
  ["Duplicate sensitivity", "Two duplicate PDF copies were tested as requested. Unique-content metrics remove only the second copy from each pair."],
  ["Excluded documents", "Two bad_resume PDFs are instructional packs containing several people, so they remain in run coverage but not role accuracy."],
  ["CSV title limitation", "The structured HTML contains job titles, but Resume_str lost layout. The production dated-employment parser found a title in 0 of 120 CSV rows."],
  ["PDF OCR limitation", "resume3.pdf is image-only and produced no extractable text. The PDF path does not perform OCR, so its role result is marked as an extraction failure."],
  ["No-match gate", "Scores below 20 require human review. A prominent non-IT professional headline uses a 35-point review threshold. The closest role remains visible as evidence only and is not a confirmed recommendation."],
  ["Human review", "Select a confirmed expected role on Review Queue. Rank, score and review result then calculate from the stored 95-role ranking without rerunning."],
];
writeMatrix(method, 5, 0, methodRows);
styleHeader(method.getRange("A5:B5"));
styleBody(method.getRange(`A6:B${4 + methodRows.length}`));
method.getRange(`B6:B${4 + methodRows.length}`).format.wrapText = true;
method.getRange(`A5:B${4 + methodRows.length}`).format.borders = {
  insideHorizontal: { style: "thin", color: colors.gray200 },
  bottom: { style: "thin", color: colors.gray300 },
};
setColumnWidth(method, "A", 24, 105);
setColumnWidth(method, "B", 92, 105);

method.getRange("A22:B22").values = [["Sources", "Citation"]];
styleHeader(method.getRange("A22:B22"));
const sourceRows = [
  ["Resume dataset", "(Source: OpenSporks Resumes dataset, local Resume_CS_Only.csv subset, CC0-1.0, https://huggingface.co/datasets/opensporks/resumes)"],
  ["PDF corpus", "(Source: Project Resume_template folder, 85 local PDF files)"],
  ["Scoring logic", "(Source: Project backend/app.py and local job catalog, 95 roles and associated requirements)"],
];
writeMatrix(method, 23, 0, sourceRows);
styleBody(method.getRange("A23:B25"));
method.getRange("B23:B25").format.wrapText = true;

method.getRange("A28:B28").values = [["Known catalog gaps", "Effect on benchmark mapping"]];
styleHeader(method.getRange("A28:B28"));
const gapRows = [
  ["DevOps Engineer", "Mapped to Cloud Computing Solution Architect / Application Consultant (Cloud) / Computer Infrastructure Specialist."],
  ["Network Administrator", "Mapped to Computer Network Technician / Network System Engineer / Network Analyst."],
  ["Python Developer", "Mapped to Software Developer / Computer Programmer / Full Stack Developer."],
  ["Web Developer", "Mapped to Full Stack Developer / Web Designer / PHP Web Programmer."],
  ["IT Infrastructure Manager", "Mapped to Computer Services Manager / Computer Infrastructure Specialist / ICT Development Manager."],
];
writeMatrix(method, 29, 0, gapRows);
styleBody(method.getRange("A29:B33"));
method.getRange("B29:B33").format.wrapText = true;

const roleList = [...new Set(rankings.map((row) => row.role_name))].sort((a, b) => a.localeCompare(b));
method.getRange("J4").values = [[`Role list (${roleList.length})`]];
method.getRange("J4").format.font = { name: font, size: 12, bold: true, color: colors.navy };
method.getRange("J5").values = [["Database role"]];
styleHeader(method.getRange("J5:J5"));
writeMatrix(method, 6, 9, roleList.map((role) => [role]));
styleBody(method.getRange(`J6:J${5 + roleList.length}`));
method.tables.add(`J5:J${5 + roleList.length}`, true, "RoleListTable").style = "TableStyleMedium2";
setColumnWidth(method, "J", 55, 105);
method.freezePanes.freezeRows(5);

// All stored role rankings (205 x 95) support later human validation without a rerun.
styleTitle(
  allRankings,
  "R",
  "All stored role rankings",
  "Each resume has one row for every database role. Recommendation Score ranks roles; component scores explain the result.",
);
const rankingHeaders = [
  "Source Key", "Source Group", "Resume / File", "Rank", "Role Name", "Recommendation Score",
  "Base Fit", "Role Evidence", "Title Match", "Title Signal Available", "Exact Title Match",
  "Keyword Overlap", "TF-IDF Similarity", "Matched Employment Title", "Title Source",
  "Explicit Non-IT Headline", "Non-IT Headline", "Scoring Version",
];
writeMatrix(allRankings, 5, 0, [rankingHeaders]);
const rankingValues = rankings.map((row) => [
  row.source_key,
  row.source_group,
  row.source_name,
  row.rank,
  row.role_name,
  row.recommendation_score,
  row.base_fit_score,
  row.role_evidence_score,
  row.title_match_score,
  row.title_match_available,
  row.exact_title_match,
  row.keyword_overlap_score,
  row.similarity_score,
  row.title_match_title,
  row.title_match_source,
  row.explicit_non_it_headline,
  row.non_it_headline,
  row.role_ranking_version,
]);
writeMatrix(allRankings, 6, 0, rankingValues);
const rankingLastRow = 5 + rankingValues.length;
styleHeader(allRankings.getRange("A5:R5"));
styleBody(allRankings.getRange(`A6:R${rankingLastRow}`));
allRankings.getRange(`D6:D${rankingLastRow}`).format.numberFormat = "0";
allRankings.getRange(`F6:I${rankingLastRow}`).format.numberFormat = "0.00";
allRankings.getRange(`L6:M${rankingLastRow}`).format.numberFormat = "0.00";
allRankings.getRange(`J6:K${rankingLastRow}`).format.horizontalAlignment = "center";
allRankings.getRange(`P6:P${rankingLastRow}`).format.horizontalAlignment = "center";
allRankings.tables.add(`A5:R${rankingLastRow}`, true, "AllRankingsTable").style = "TableStyleMedium2";
const rankingWidths = [26, 20, 42, 8, 50, 18, 13, 14, 13, 18, 15, 16, 16, 44, 14, 20, 34, 14];
rankingWidths.forEach((width, index) => setColumnWidth(allRankings, colLetter(index), width, rankingLastRow));
allRankings.freezePanes.freezeRows(5);
allRankings.freezePanes.freezeColumns(1);

// Main one-row-per-source audit table.
styleTitle(
  allResults,
  "AS",
  "All resume results",
  "All 205 sources are present. Accuracy fields use independent labels; unlabelled rows stay in human review instead of being auto-approved.",
);
const resultHeaders = [
  "Source Key", "Source Group", "Source Type", "Category / Folder", "Resume / File", "Independent Source Title",
  "Run Status", "Extraction Status", "Pages", "Words", "Label Status", "Label Confidence", "Expected Family",
  "Expected Primary Role", "Acceptable Roles", "Top 1 Role", "Top 1 Score", "Top 2 Role", "Top 2 Score",
  "Top 3 Role", "Top 3 Score", "Best Acceptable Rank", "Best Acceptable Role", "Best Acceptable Score",
  "Outcome", "Parsed Recent Job Title", "Title Signal Available", "Base Fit", "Role Evidence", "Title Match",
  "Top 1 Margin", "Duplicate Group", "Duplicate Copy", "Label Note", "Human Expected Role", "Human Review Status",
  "Human Notes",
  "Scoring Version", "Top 1 Matched Employment Title", "Top 1 Title Source",
  "Explicit Non-IT Headline", "Non-IT Headline", "Review Threshold", "Human Review Required", "System Disposition",
];
writeMatrix(allResults, 5, 0, [resultHeaders]);
const resultValues = rows.map((row) => [
  row.source_key,
  row.source_group,
  row.source_type,
  row.source_category,
  row.source_name,
  row.source_title,
  row.run_status,
  row.extraction_status,
  row.page_count,
  row.word_count,
  row.label_status,
  row.label_confidence,
  row.expected_family,
  row.expected_primary_role,
  row.acceptable_roles,
  row.top1_role,
  row.top1_score,
  row.top2_role,
  row.top2_score,
  row.top3_role,
  row.top3_score,
  row.best_acceptable_rank,
  row.best_acceptable_role,
  row.best_acceptable_score,
  row.outcome,
  row.most_recent_job_title,
  row.title_match_available,
  row.top1_base_fit,
  row.top1_role_evidence,
  row.top1_title_match,
  row.top1_margin,
  row.duplicate_group,
  row.is_duplicate_copy,
  row.label_note,
  "",
  "UNREVIEWED",
  "",
  row.role_ranking_version,
  row.top1_title_match_title,
  row.top1_title_match_source,
  row.top1_explicit_non_it_headline,
  row.top1_non_it_headline,
  row.review_threshold,
  row.human_review_required,
  row.system_disposition,
]);
writeMatrix(allResults, 6, 0, resultValues);
const resultsLastRow = 5 + resultValues.length;
styleHeader(allResults.getRange("A5:AS5"));
styleBody(allResults.getRange(`A6:AS${resultsLastRow}`));
allResults.getRange(`Q6:Q${resultsLastRow}`).format.numberFormat = "0.00";
allResults.getRange(`S6:S${resultsLastRow}`).format.numberFormat = "0.00";
allResults.getRange(`U6:U${resultsLastRow}`).format.numberFormat = "0.00";
allResults.getRange(`V6:V${resultsLastRow}`).format.numberFormat = "0";
allResults.getRange(`X6:X${resultsLastRow}`).format.numberFormat = "0.00";
allResults.getRange(`AB6:AE${resultsLastRow}`).format.numberFormat = "0.00";
allResults.getRange(`I6:J${resultsLastRow}`).format.numberFormat = "0";
allResults.getRange(`AA6:AA${resultsLastRow}`).format.horizontalAlignment = "center";
allResults.getRange(`AG6:AG${resultsLastRow}`).format.horizontalAlignment = "center";
allResults.getRange(`AO6:AO${resultsLastRow}`).format.horizontalAlignment = "center";
allResults.getRange(`AQ6:AQ${resultsLastRow}`).format.numberFormat = "0";
allResults.getRange(`AR6:AR${resultsLastRow}`).format.horizontalAlignment = "center";
allResults.getRange(`AI6:AK${resultsLastRow}`).format.fill = colors.paleAmber;
allResults.getRange(`AI6:AI${resultsLastRow}`).dataValidation = {
  rule: { type: "list", formula1: `'Method & Roles'!$J$6:$J$${5 + roleList.length}` },
};
allResults.getRange(`AJ6:AJ${resultsLastRow}`).dataValidation = {
  rule: { type: "list", values: ["UNREVIEWED", "CONFIRMED", "NEEDS DISCUSSION"] },
};
allResults.getRange(`H6:H${resultsLastRow}`).conditionalFormats.add("containsText", {
  text: "FAILED",
  format: { fill: colors.paleRed, font: { color: colors.red, bold: true } },
});
allResults.getRange(`Y6:Y${resultsLastRow}`).conditionalFormats.add("containsText", {
  text: "PASS",
  format: { fill: colors.paleGreen, font: { color: colors.green, bold: true } },
});
for (const warning of ["MISS", "FAILED", "FORCED", "FALSE ROLE"]) {
  allResults.getRange(`Y6:Y${resultsLastRow}`).conditionalFormats.add("containsText", {
    text: warning,
    format: { fill: colors.paleRed, font: { color: colors.red, bold: true } },
  });
}
for (const pending of ["HUMAN", "UNREVIEWED", "NO SUITABLE"]) {
  allResults.getRange(`Y6:Y${resultsLastRow}`).conditionalFormats.add("containsText", {
    text: pending,
    format: { fill: colors.paleAmber, font: { color: colors.amber, bold: true } },
  });
}
allResults.getRange(`AS6:AS${resultsLastRow}`).conditionalFormats.add("containsText", {
  text: "NO SUITABLE ROLE",
  format: { fill: colors.paleAmber, font: { color: colors.amber, bold: true } },
});
allResults.tables.add(`A5:AS${resultsLastRow}`, true, "AllResultsTable").style = "TableStyleMedium2";
const resultWidths = [
  30, 20, 17, 22, 45, 44, 13, 27, 8, 10, 27, 16, 24, 45, 80, 46, 13, 46, 13,
  46, 13, 18, 46, 18, 22, 44, 18, 12, 14, 12, 13, 16, 14, 62, 46, 20, 55, 14, 44, 14,
  20, 34, 16, 18, 24,
];
resultWidths.forEach((width, index) => setColumnWidth(allResults, colLetter(index), width, resultsLastRow));
allResults.getRange(`M6:O${resultsLastRow}`).format.wrapText = true;
allResults.getRange(`AH6:AH${resultsLastRow}`).format.wrapText = true;
allResults.freezePanes.freezeRows(5);
allResults.freezePanes.freezeColumns(5);

// Human review workflow. Source and system fields are prefilled; only expected role and notes need input.
const reviewRows = rows.filter((row) => ["HUMAN_REVIEW_REQUIRED", "PROVISIONAL_CSV_FAMILY"].includes(row.label_status));
styleTitle(
  reviewQueue,
  "K",
  "Human review queue",
  "Select Confirmed Expected Role and add notes. Confirmed Rank, Score and Result calculate from the stored 95-role ranking.",
);
const reviewHeaders = [
  "Source Key", "Independent Source Title", "Suggested Family", "Current Top 1 Role", "Top 1 Score",
  "Confirmed Expected Role (input)", "Confirmed Rank", "Confirmed Score", "Review Result", "Reviewer Notes (input)", "Resume / ID",
];
writeMatrix(reviewQueue, 5, 0, [reviewHeaders]);
const reviewValues = reviewRows.map((row) => [
  row.source_key,
  row.source_title,
  row.expected_family,
  row.top1_role,
  row.top1_score,
  "",
  null,
  null,
  "UNREVIEWED",
  "",
  row.source_name,
]);
writeMatrix(reviewQueue, 6, 0, reviewValues);
const reviewLastRow = 5 + reviewValues.length;
const reviewFormulas = reviewRows.map((_, index) => {
  const rowNumber = index + 6;
  return [
    `=IF(F${rowNumber}="","",SUMIFS('All Rankings'!$D$6:$D$${rankingLastRow},'All Rankings'!$A$6:$A$${rankingLastRow},$A${rowNumber},'All Rankings'!$E$6:$E$${rankingLastRow},$F${rowNumber}))`,
    `=IF(F${rowNumber}="","",SUMIFS('All Rankings'!$F$6:$F$${rankingLastRow},'All Rankings'!$A$6:$A$${rankingLastRow},$A${rowNumber},'All Rankings'!$E$6:$E$${rankingLastRow},$F${rowNumber}))`,
    `=IF(F${rowNumber}="","UNREVIEWED",IF(G${rowNumber}=1,"PASS TOP 1",IF(G${rowNumber}<=3,"PASS TOP 3","MISS TOP 3")))`,
  ];
});
reviewQueue.getRange(`G6:I${reviewLastRow}`).formulas = reviewFormulas;
styleHeader(reviewQueue.getRange("A5:K5"));
styleBody(reviewQueue.getRange(`A6:K${reviewLastRow}`));
reviewQueue.getRange(`E6:E${reviewLastRow}`).format.numberFormat = "0.00";
reviewQueue.getRange(`G6:G${reviewLastRow}`).format.numberFormat = "0";
reviewQueue.getRange(`H6:H${reviewLastRow}`).format.numberFormat = "0.00";
reviewQueue.getRange(`F6:F${reviewLastRow}`).format.fill = colors.paleAmber;
reviewQueue.getRange(`J6:J${reviewLastRow}`).format.fill = colors.paleAmber;
reviewQueue.getRange(`G6:I${reviewLastRow}`).format.fill = colors.paleBlue;
reviewQueue.getRange(`F6:F${reviewLastRow}`).dataValidation = {
  rule: { type: "list", formula1: `'Method & Roles'!$J$6:$J$${5 + roleList.length}` },
};
reviewQueue.getRange(`I6:I${reviewLastRow}`).conditionalFormats.add("containsText", {
  text: "PASS",
  format: { fill: colors.paleGreen, font: { color: colors.green, bold: true } },
});
reviewQueue.getRange(`I6:I${reviewLastRow}`).conditionalFormats.add("containsText", {
  text: "MISS",
  format: { fill: colors.paleRed, font: { color: colors.red, bold: true } },
});
reviewQueue.getRange(`I6:I${reviewLastRow}`).conditionalFormats.add("containsText", {
  text: "UNREVIEWED",
  format: { fill: colors.paleAmber, font: { color: colors.amber, bold: true } },
});
reviewQueue.tables.add(`A5:K${reviewLastRow}`, true, "ReviewQueueTable").style = "TableStyleMedium2";
const reviewWidths = [30, 46, 26, 46, 13, 48, 16, 17, 18, 56, 30];
reviewWidths.forEach((width, index) => setColumnWidth(reviewQueue, colLetter(index), width, reviewLastRow));
reviewQueue.freezePanes.freezeRows(5);
reviewQueue.freezePanes.freezeColumns(1);

// Role-level diagnostic table.
styleTitle(
  roleMetricsSheet,
  "L",
  "Accuracy by independent expected role",
  "Validated and provisional groups remain separate. A broad acceptable-role set is used where the exact source title is absent from the 95-role catalog.",
);
const metricHeaders = [
  "Label Status", "Expected Family", "Expected Primary Role", "Cases", "Top 1 Passes", "Top 1 Accuracy",
  "Top 3 Passes", "Top 3 Accuracy", "Mean Best Rank", "MRR", "Average Top 1 Score", "Average Expected Score",
];
writeMatrix(roleMetricsSheet, 5, 0, [metricHeaders]);
const metricValues = roleMetrics.map((row) => [
  row.label_status,
  row.expected_family,
  row.expected_primary_role,
  row.cases,
  row.top1_passes,
  percentOrBlank(row.top1_accuracy),
  row.top3_passes,
  percentOrBlank(row.top3_accuracy),
  row.mean_best_rank,
  percentOrBlank(row.mrr),
  row.average_top1_score,
  row.average_expected_score,
]);
writeMatrix(roleMetricsSheet, 6, 0, metricValues);
const metricLastRow = 5 + metricValues.length;
styleHeader(roleMetricsSheet.getRange("A5:L5"));
styleBody(roleMetricsSheet.getRange(`A6:L${metricLastRow}`));
roleMetricsSheet.getRange(`F6:F${metricLastRow}`).format.numberFormat = "0.0%";
roleMetricsSheet.getRange(`H6:H${metricLastRow}`).format.numberFormat = "0.0%";
roleMetricsSheet.getRange(`J6:J${metricLastRow}`).format.numberFormat = "0.0%";
roleMetricsSheet.getRange(`I6:I${metricLastRow}`).format.numberFormat = "0.00";
roleMetricsSheet.getRange(`K6:L${metricLastRow}`).format.numberFormat = "0.00";
roleMetricsSheet.tables.add(`A5:L${metricLastRow}`, true, "RoleMetricsTable").style = "TableStyleMedium2";
const metricWidths = [28, 28, 52, 10, 14, 16, 14, 16, 17, 12, 20, 22];
metricWidths.forEach((width, index) => setColumnWidth(roleMetricsSheet, colLetter(index), width, metricLastRow));
roleMetricsSheet.freezePanes.freezeRows(5);

// Summary uses formulas linked to All Results so the reported rates remain auditable.
styleTitle(
  summary,
  "Q",
  "Resume-to-role batch evaluation",
  "205 sources were ranked against all 95 database roles. Validated accuracy, provisional diagnostics and extraction coverage are reported separately.",
);
const cardBlocks = [
  { range: "A5:C7", labelCell: "A5", valueCell: "A6", label: "Total sources", formula: `=COUNTA('All Results'!$A$6:$A$${resultsLastRow})` },
  { range: "D5:F7", labelCell: "D5", valueCell: "D6", label: "Completed rankings", formula: `=COUNTIFS('All Results'!$G$6:$G$${resultsLastRow},"PROCESSED")` },
  { range: "G5:I7", labelCell: "G5", valueCell: "G6", label: "Text extracted", formula: `=COUNTIFS('All Results'!$H$6:$H$${resultsLastRow},"OK")` },
  { range: "J5:L7", labelCell: "J5", valueCell: "J6", label: "Validated templates", formula: `=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE")` },
];
for (const block of cardBlocks) {
  summary.getRange(block.range).format = {
    fill: colors.paleBlue,
    borders: { preset: "outside", style: "thin", color: colors.gray300 },
  };
  summary.getRange(block.labelCell).values = [[block.label]];
  summary.getRange(block.labelCell).format.font = { name: font, size: 10, bold: true, color: colors.gray700 };
  summary.getRange(block.valueCell).formulas = [[block.formula]];
  summary.getRange(block.valueCell).format.font = { name: font, size: 16, bold: true, color: colors.navy };
  summary.getRange(block.valueCell).format.numberFormat = "#,##0";
}

summary.getRange("A9:F9").values = [["Benchmark", "Cases", "Top 1", "Top 3", "MRR", "Interpretation"]];
styleHeader(summary.getRange("A9:F9"));
const benchmarkNames = [
  "Mapped templates (all files)",
  "Mapped templates (unique content)",
  "Real resumes (text extracted)",
  "CSV exact source-title cases",
  "CSV provisional family cases",
];
const benchmarkNotes = [
  "Validated mapped-role benchmark",
  "Removes two duplicate copies",
  "Provisional; 4 readable real resumes",
  "Provisional; exact source title exists in catalog",
  "Provisional; broad family mapping",
];
const benchmarkMrr = [
  summaryData.validated_template_raw.mrr,
  summaryData.validated_template_unique.mrr,
  summaryData.provisional_real_extracted.mrr,
  summaryData.provisional_csv_exact.mrr,
  summaryData.provisional_csv_family.mrr,
];
writeMatrix(summary, 10, 0, benchmarkNames.map((name, index) => [name, null, null, null, benchmarkMrr[index], benchmarkNotes[index]]));
for (let index = 0; index < benchmarkNames.length; index += 1) {
  const rowNumber = index + 10;
  if (index === 0) {
    summary.getRange(`B${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE")`]];
    summary.getRange(`C${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE",'All Results'!$V$6:$V$${resultsLastRow},1)/B${rowNumber}`]];
    summary.getRange(`D${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE",'All Results'!$V$6:$V$${resultsLastRow},"<=3")/B${rowNumber}`]];
  } else if (index === 1) {
    // Artifact Tool's current COUNTIFS engine does not match stored Boolean FALSE
    // reliably. These values come from the same audited rows after removing only
    // the second copy in each duplicate pair.
    summary.getRange(`B${rowNumber}:D${rowNumber}`).values = [[
      summaryData.validated_template_unique.cases,
      summaryData.validated_template_unique.top1_accuracy,
      summaryData.validated_template_unique.top3_accuracy,
    ]];
  } else if (index === 2) {
    summary.getRange(`B${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_REAL_RESUME",'All Results'!$H$6:$H$${resultsLastRow},"OK")`]];
    summary.getRange(`C${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_REAL_RESUME",'All Results'!$H$6:$H$${resultsLastRow},"OK",'All Results'!$V$6:$V$${resultsLastRow},1)/B${rowNumber}`]];
    summary.getRange(`D${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_REAL_RESUME",'All Results'!$H$6:$H$${resultsLastRow},"OK",'All Results'!$V$6:$V$${resultsLastRow},"<=3")/B${rowNumber}`]];
  } else if (index === 3) {
    summary.getRange(`B${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_CSV_EXACT")`]];
    summary.getRange(`C${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_CSV_EXACT",'All Results'!$V$6:$V$${resultsLastRow},1)/B${rowNumber}`]];
    summary.getRange(`D${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_CSV_EXACT",'All Results'!$V$6:$V$${resultsLastRow},"<=3")/B${rowNumber}`]];
  } else {
    summary.getRange(`B${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_CSV_FAMILY")`]];
    summary.getRange(`C${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_CSV_FAMILY",'All Results'!$V$6:$V$${resultsLastRow},1)/B${rowNumber}`]];
    summary.getRange(`D${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"PROVISIONAL_CSV_FAMILY",'All Results'!$V$6:$V$${resultsLastRow},"<=3")/B${rowNumber}`]];
  }
}
styleBody(summary.getRange("A10:F14"));
summary.getRange("B10:B14").format.numberFormat = "#,##0";
summary.getRange("C10:E14").format.numberFormat = "0.0%";
summary.getRange("A9:F14").format.borders = {
  insideHorizontal: { style: "thin", color: colors.gray200 },
  bottom: { style: "thin", color: colors.gray300 },
};

summary.getRange("H9:Q9").merge();
summary.getRange("H9").values = [["Key findings"]];
summary.getRange("H9:Q9").format = {
  fill: colors.navy,
  font: { name: font, size: 10, bold: true, color: colors.white },
  verticalAlignment: "center",
};
const negativeScores = summaryData.negative_control_top1_scores || [];
const negativeRange = negativeScores.length
  ? `${Math.min(...negativeScores).toFixed(1)} to ${Math.max(...negativeScores).toFixed(1)}`
  : "not available";
const csvExactCases = summaryData.provisional_csv_exact?.cases || 0;
const csvExactTop3 = summaryData.provisional_csv_exact?.top3_passes || 0;
const findingTexts = [
  `Mapped templates: ${Math.round(summaryData.validated_template_raw.top1_accuracy * 1000) / 10}% Top-1 and ${Math.round(summaryData.validated_template_raw.top3_accuracy * 1000) / 10}% Top-3.`,
  `Unique mapped templates: ${Math.round(summaryData.validated_template_unique.top1_accuracy * 1000) / 10}% Top-1 and ${Math.round(summaryData.validated_template_unique.top3_accuracy * 1000) / 10}% Top-3 after duplicate copies are removed.`,
  `CSV title structure was lost: the production parser found ${summaryData.csv_title_parser_available} of ${summaryData.csv_sources} dated current titles; ${csvExactTop3} of ${csvExactCases} exact source-title cases reached the Top 3.`,
  `${summaryData.negative_controls_review_required} of ${summaryData.negative_control_cases} out-of-domain controls now return NO SUITABLE ROLE; closest-role evidence range ${negativeRange}.`,
  "One image-only PDF had no extractable text. PDF OCR is not implemented in the current upload path.",
  "Recommendation Score ranks evidence. It should not be read as a hiring probability or a validated accuracy percentage.",
];
findingTexts.forEach((finding, index) => {
  const rowNumber = 10 + index;
  summary.mergeCells(`H${rowNumber}:Q${rowNumber}`);
  summary.getRange(`H${rowNumber}`).values = [[finding]];
  summary.getRange(`H${rowNumber}:Q${rowNumber}`).format = {
    fill: index === 3 ? colors.paleGreen : (index === 2 || index === 4 ? colors.paleAmber : colors.gray100),
    font: { name: font, size: 10, color: colors.gray900 },
    wrapText: true,
    verticalAlignment: "center",
    borders: { bottom: { style: "thin", color: colors.gray200 } },
  };
  summary.getRange(`H${rowNumber}:Q${rowNumber}`).format.rowHeight = 28;
});

summary.getRange("A18:G18").values = [["Template folder", "Cases", "Top 1 Passes", "Top 1 Accuracy", "Top 3 Passes", "Top 3 Accuracy", "Average Top 1 Score"]];
styleHeader(summary.getRange("A18:G18"));
const folderOrder = [...new Set(rows.filter((row) => row.label_status === "VALIDATED_TEMPLATE").map((row) => row.source_category))]
  .map((folder) => {
    const group = rows.filter((row) => row.label_status === "VALIDATED_TEMPLATE" && row.source_category === folder);
    return { folder, rate: group.filter((row) => row.top1_acceptable).length / group.length };
  })
  .sort((a, b) => b.rate - a.rate || a.folder.localeCompare(b.folder));
const folderLabels = {
  cybersecurity: "Cybersecurity",
  devops_it_operations: "DevOps / IT operations",
  fullstack: "Full stack",
  it_manager: "IT manager",
  java_developer: "Java developer",
  network_admin: "Network administrator",
  network_engineer: "Network engineer",
  python: "Python developer",
  qa: "Quality assurance",
  software_developer: "Software developer",
  software_engineer: "Software engineer",
  web_developer: "Web developer",
};
writeMatrix(summary, 19, 0, folderOrder.map((item) => [folderLabels[item.folder] ?? item.folder, null, null, null, null, null, null]));
folderOrder.forEach((item, index) => {
  const rowNumber = 19 + index;
  summary.getRange(`B${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE",'All Results'!$D$6:$D$${resultsLastRow},"${item.folder}")`]];
  summary.getRange(`C${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE",'All Results'!$D$6:$D$${resultsLastRow},"${item.folder}",'All Results'!$V$6:$V$${resultsLastRow},1)`]];
  summary.getRange(`D${rowNumber}`).formulas = [[`=C${rowNumber}/B${rowNumber}`]];
  summary.getRange(`E${rowNumber}`).formulas = [[`=COUNTIFS('All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE",'All Results'!$D$6:$D$${resultsLastRow},"${item.folder}",'All Results'!$V$6:$V$${resultsLastRow},"<=3")`]];
  summary.getRange(`F${rowNumber}`).formulas = [[`=E${rowNumber}/B${rowNumber}`]];
  summary.getRange(`G${rowNumber}`).formulas = [[`=AVERAGEIFS('All Results'!$Q$6:$Q$${resultsLastRow},'All Results'!$K$6:$K$${resultsLastRow},"VALIDATED_TEMPLATE",'All Results'!$D$6:$D$${resultsLastRow},"${item.folder}")`]];
});
const folderLastRow = 18 + folderOrder.length;
styleBody(summary.getRange(`A19:G${folderLastRow}`));
summary.getRange(`B19:C${folderLastRow}`).format.numberFormat = "0";
summary.getRange(`E19:E${folderLastRow}`).format.numberFormat = "0";
summary.getRange(`D19:D${folderLastRow}`).format.numberFormat = "0.0%";
summary.getRange(`F19:F${folderLastRow}`).format.numberFormat = "0.0%";
summary.getRange(`G19:G${folderLastRow}`).format.numberFormat = "0.00";
summary.getRange(`A18:G${folderLastRow}`).format.borders = {
  insideHorizontal: { style: "thin", color: colors.gray200 },
  bottom: { style: "thin", color: colors.gray300 },
};

const chart = summary.charts.add("bar", [
  summary.getRange(`A18:A${folderLastRow}`),
  summary.getRange(`D18:D${folderLastRow}`),
  summary.getRange(`F18:F${folderLastRow}`),
]);
chart.title = "Mapped template accuracy by folder";
chart.titleTextStyle.fontSize = 12;
chart.titleTextStyle.typeface = font;
chart.legend = { position: "top", textStyle: { typeface: font, fontSize: 10 } };
chart.xAxis = { axisType: "textAxis", textStyle: { typeface: font, fontSize: 10 } };
chart.yAxis = { numberFormatCode: "0%", numberFormatSourceLinked: false, textStyle: { typeface: font, fontSize: 10 } };
chart.setPosition("I18", "Q37");
if (chart.series.items.length >= 2) {
  chart.series.items[0].fill = colors.blue;
  chart.series.items[1].fill = colors.blue2;
}

summary.getRange("A34:G37").values = [
  ["Coverage checks", "Value", "Meaning", null, null, null, null],
  ["PDF pages above one", summaryData.pdf_mult_page, "Every extractable PDF page was processed; this value counts multi-page PDF sources.", null, null, null, null],
  ["CSV titles parsed by production", summaryData.csv_title_parser_available, `${summaryData.csv_title_parser_available} of ${summaryData.csv_sources}; the plain-text dataset generally loses dated employment layout.`, null, null, null, null],
  ["Out-of-domain controls sent to review", summaryData.negative_controls_review_required, `${summaryData.negative_controls_review_required} of ${summaryData.negative_control_cases} return NO SUITABLE ROLE under the version 2.2 review gate.`, null, null, null, null],
];
summary.mergeCells("C34:G34");
for (const rowNumber of [35, 36, 37]) {
  summary.mergeCells(`C${rowNumber}:G${rowNumber}`);
}
styleHeader(summary.getRange("A34:G34"));
styleBody(summary.getRange("A35:G37"));
summary.getRange("C35:C37").format.wrapText = true;
summary.getRange("C35:G37").format.rowHeight = 28;
summary.getRange("A34:G37").format.borders = {
  insideHorizontal: { style: "thin", color: colors.gray200 },
  bottom: { style: "thin", color: colors.gray300 },
};

setColumnWidth(summary, "A", 38, 40);
setColumnWidth(summary, "B", 12, 40);
setColumnWidth(summary, "C", 16, 40);
setColumnWidth(summary, "D", 17, 40);
setColumnWidth(summary, "E", 15, 40);
setColumnWidth(summary, "F", 44, 40);
setColumnWidth(summary, "G", 20, 40);
for (const column of ["H", "I", "J", "K", "L", "M", "N", "O", "P", "Q"]) {
  setColumnWidth(summary, column, 12, 40);
}

// One final calculation precedes all final inspection, rendering, and export.
workbook.recalculate();

// Verify the editable review workflow in memory, then restore the delivered
// row to UNREVIEWED before the final calculation and export.
const reviewWorkflowRole = reviewRows[0]?.top1_role || "";
reviewQueue.getRange("F6").values = [[reviewWorkflowRole]];
workbook.recalculate();
const reviewWorkflowValues = reviewQueue.getRange("F6:I6").values[0];
if (
  Number(reviewWorkflowValues[1]) !== 1
  || Math.abs(Number(reviewWorkflowValues[2]) - Number(reviewRows[0]?.top1_score || 0)) > 0.01
  || String(reviewWorkflowValues[3]) !== "PASS TOP 1"
) {
  throw new Error(`Review Queue recalc test failed: ${JSON.stringify(reviewWorkflowValues)}`);
}
reviewQueue.getRange("F6").values = [[""]];
workbook.recalculate();
if (String(reviewQueue.getRange("I6").values[0][0]) !== "UNREVIEWED") {
  throw new Error("Review Queue restore test failed: row 6 did not return to UNREVIEWED.");
}

const summaryInspect = await workbook.inspect({
  kind: "region",
  sheetId: "Summary",
  range: "A1:Q37",
  maxChars: 9000,
});
const reviewInspect = await workbook.inspect({
  kind: "region",
  sheetId: "Review Queue",
  range: "A1:K18",
  maxChars: 5000,
});
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

await fs.mkdir(previewDir, { recursive: true });
const previewSpecs = [
  ["Summary", "A1:Q37"],
  ["All Results", "A1:AS20"],
  ["Review Queue", "A1:K22"],
  ["Role Metrics", `A1:L${Math.min(metricLastRow, 28)}`],
  ["All Rankings", "A1:R22"],
  ["Method & Roles", "A1:K36"],
];
for (const [sheetName, range] of previewSpecs) {
  const image = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const safeName = sheetName.toLowerCase().replaceAll(/[^a-z0-9]+/g, "_");
  await fs.writeFile(path.join(previewDir, `${safeName}.png`), new Uint8Array(await image.arrayBuffer()));
}

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const savedSheets = await saved.inspect({ kind: "sheet,drawing,table", maxChars: 6000, tableMaxRows: 3, tableMaxCols: 5 });
const savedErrors = await saved.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "saved workbook formula error scan",
});

const qa = {
  outputPath,
  resultRows: rows.length,
  rankingRows: rankings.length,
  reviewRows: reviewRows.length,
  roleCount: roleList.length,
  reviewWorkflowTest: {
    selectedRole: reviewWorkflowRole,
    calculatedRank: Number(reviewWorkflowValues[1]),
    calculatedScore: Number(reviewWorkflowValues[2]),
    calculatedResult: String(reviewWorkflowValues[3]),
    restoredResult: String(reviewQueue.getRange("I6").values[0][0]),
  },
  summaryInspect: summaryInspect.ndjson,
  reviewInspect: reviewInspect.ndjson,
  formulaErrors: formulaErrors.ndjson,
  savedSheets: savedSheets.ndjson,
  savedErrors: savedErrors.ndjson,
};
await fs.writeFile(path.join(outputDir, "workbook_qa.json"), JSON.stringify(qa, null, 2), "utf8");
console.log(JSON.stringify({ outputPath, resultRows: rows.length, rankingRows: rankings.length, reviewRows: reviewRows.length, roleCount: roleList.length }, null, 2));
