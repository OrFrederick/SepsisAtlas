**✨ EXAMPLE OF OTHER USE CASES — EXTRA POINTS**

**Supported Query Types**
• biomarker/score → mortality association
• predictor comparison across studies
• phenotype extraction from papers

---

**🧠 EXAMPLE QUERY**

*"What is the relationship between lactate levels and 28-day mortality in septic shock?"*

The following examples illustrate how structured evidence extraction from clinical literature can support real-world research workflows. These use cases are provided to guide participants and demonstrate the potential applications of their systems.

---

**🟢 USE CASE 2: SEPSIS PHENOTYPE EXTRACTION**

**Context**
Sepsis is a highly heterogeneous condition. Multiple studies propose different patient phenotypes based on clustering of latent structure.

**Objective**
Identify published sepsis phenotypes and assess whether they can be applied to an external patient cohort.

**Role of the AI System**
The system should extract:
• phenotype identification methods *(e.g., k-means, latent class analysis)*
• variables used for clustering
• number of clusters
• cluster characteristics *(means, medians, distributions)*
• clinical interpretation of clusters
• outcomes per phenotype

**Expected Output**
A structured representation of phenotype definitions, for example:
• Cluster A: low severity, low mortality
• Cluster B: high inflammation, high mortality
• Cluster C: organ failure dominant

with associated quantitative descriptions.

```
STUDY-LEVEL SUMMARY
---------------------------------------------------------------------------------------------------------------
| Study           | Country | Setting | Sample Size | Sepsis Def | Method            | Clusters | Variables |
|-----------------|---------|---------|-------------|------------|-------------------|----------|-----------|
| Donzelli 2019   | Norway  | ICU     | N=1476      | Sepsis-3   | k-means clustering| 4 (A–D)  | 18 vars   |
| ...             | ...     | ...     | ...         | ...        | ...               | ...      | ...       |
---------------------------------------------------------------------------------------------------------------


PHENOTYPE (CLUSTER-LEVEL) TABLE
----------------------------------------------------------------------------------------------------------------------------------
| Study         | Cluster | Key Features                      | Clinical Description        | Outcomes              | Notes                  |
|---------------|---------|-----------------------------------|-----------------------------|----------------------|------------------------|
| Donzelli 2019 | A       | Platelets↓, Lactate↓, SOFA↓       | Low severity phenotype      | ICU mortality ~12%   | Mild inflammation      |
| Donzelli 2019 | B       | Mixed markers                     | Moderate severity           | Mortality ...        | Mixed inflammation     |
| Donzelli 2019 | C       | Lactate↑, Procalcitonin↑          | High inflammation phenotype | Mortality ...        | Elevated biomarkers    |
| Donzelli 2019 | D       | SOFA↑, Lactate↑                   | Severe organ dysfunction    | Highest mortality    | High SOFA, lactate     |
| ...           | ...     | ...                               | ...                         | ...                  | ...                    |
----------------------------------------------------------------------------------------------------------------------------------
```

**Reference table images (from brief):**

*Study-Level Summary table (Donatello 2019 example):*
Columns: Study | Country | Setting | Sample Size | Sepsis Definition | ... | Method | Num Clusters | Variables Used | External Assignment Feasible | Source
Row: Donatello 2019 | Norway | ICU | N=14,768 | Sepsis-3 | ... | k-means clustering | 4 (A–D) | 18 variables (...) | ... | ...

*Phenotype (Cluster-Level) Table (Donatello 2019 example):*
Columns: Study | Cluster | Size | Key Features (Central Tendencies) | ... | Clinical Description | Outcomes | Notes | Source
- A | N=... | Platelets: mean ...; Lactate: mean ...; SOFA: median ...; Procalcitonin: median ... | Low severity phenotype | ICU mortality ~12%; shorter ICU stay | Mild inflammation profile | [p. X, Table Y]
- B | N=... | Platelets/Lactate/SOFA/Procalcitonin: ... | Moderate severity | Mortality ... | Mixed inflammatory profile | [p. X]
- C | N=... | Platelets/Lactate/SOFA/Procalcitonin: ... | High inflammation phenotype | Mortality ... | Elevated biomarkers | [p. X]
- D | N=... | Platelets/Lactate/SOFA/Procalcitonin: ... | Severe organ dysfunction | Highest mortality ... | High SOFA, high lactate | [p. X]

**⚠️ IMPORTANT NOTE**

In many studies, phenotype assignment rules may not be fully reproducible.
The system should explicitly indicate:

• whether assignment is possible
• or whether information is insufficient

---

**📊 OUTCOME**

Supports feasibility assessment and downstream modeling for phenotype-based analysis.

---

**🟢 USE CASE 3: BIOMARKER SELECTION FOR RISK STRATIFICATION**

**Context**
A clinical trial requires selecting a single biomarker or score to stratify patients by mortality risk.

**Objective**
Identify which clinical variables have the strongest prognostic value for 28-day mortality.

**Role of the AI System**
The system should extract and compare across studies:
• biomarkers *(e.g., lactate, IL-6, CRP)*
• clinical scores *(e.g., SOFA, APACHE)*
• effect sizes *(AUC, OR, HR)*
• statistical models
• validation methods
• cohort characteristics

**Expected Output**
A structured comparison table enabling:
• ranking of predictors
• comparison across studies
• filtering by population relevance

**Reference table images (from brief):**

*Evidence Table:*
Columns: Study | Population | Sample Size | Predictor | Outcome | ... | Model | Effect Size | Performance | Adjustment | Relevance to Target Population | Source
- Raphael 2024 | ICU patients, Sepsis-3 (MIMIC-IV, UK) | N=5122 | SOFA score | 28-day mortality | ... | Logistic regression (stepwise) | — | AUC 0.72 (95% CI 0.70–0.74) | Not specified | Medium (ICU sepsis, not limited to septic shock) | [p. X, Results]
- Raphael 2024 | ICU patients, Sepsis-3 | N=5122 | Lactate (>4 mmol/L) | 28-day mortality | ... | Logistic regression | OR 3.1 (95% CI 2.4–4.0) | — | Not specified | High (shock-related marker, relevant physiology) | [p. X]
- Raphael 2024 | ICU patients, Sepsis-3 | N=5122 | SOFA + Lactate | 28-day mortality | ... | Logistic regression | — | AUC 0.76; Pseudo-R² 0.28 | Combined model | High | [p. X]
- Donatello 2025 | Septic patients (Germany, prospective) | N=450 | IL-6 (>1000 pg/mL) | 28-day mortality | ... | Cox proportional hazards | HR 4.5 (95% CI 3.1–6.2) | C-index 0.86 | Adjusted (age, sex, Charlson index) | High (severe patients, likely closer to shock cohort) | [p. Y]

*Ranked Predictors:*
Columns: Predictor | Best Metric | Value | ... | Study | Notes
- IL-6 | C-index | 0.86 | ... | Donatello 2025 | Strong prognostic signal, adjusted model
- SOFA + Lactate | AUC | 0.76 | ... | Raphael 2024 | Combined model improves performance
- SOFA | AUC | 0.72 | ... | Raphael 2024 | Standard severity score
- Lactate (>4 mmol/L) | OR | 3.1 | ... | Raphael 2024 | Strong univariate predictor

**📊 OUTCOME**

Supports evidence-based selection of stratification variables.
