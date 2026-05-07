**🧬 SEPSIS ATLAS HACKATHON**
*Paper-to-Knowledge Hackathon*

Sepsis atlas hackathon is a challenge to build AI pipelines that transform unstructured clinical sepsis papers into structured, verifiable, analysis-ready data. Participants will create systems that extract key study variables, generate clean dataframes, and link every value back to its exact source in the paper.

---

**🟢 CHALLENGE**

Effective sepsis research and treatment require understanding patient heterogeneity, disease trajectories, and response to therapy. However, building such understanding is limited by data availability. Individual clinical datasets are small, expensive to collect, and often lack generalizability across populations and treatment settings.

At the same time, vast amounts of clinically relevant data already exist — embedded within thousands of published medical studies. Traditional meta-analyses leverage this information but are constrained by predefined hypotheses and require labor-intensive manual data extraction, making large-scale synthesis slow and non-cumulative.

We challenge you to reverse this paradigm.
Your task is to build an AI pipeline that transforms published sepsis research into a structured, growing evidence base — a **"Sepsis Atlas."**

The system should act as an AI-powered clinical evidence assistant, going beyond text-based answers to extract, structure, and present **verifiable, source-grounded data** from scientific articles.

The system may use a chat interface or any other simple interaction format, but its core objective is to transform unstructured literature into **analysis-ready, structured data.**

Given a natural language query (e.g., *"What is the relationship between initial lactate level and 28-day mortality in septic shock?"*), the system should return an **analysis-ready table** aggregating relevant results across studies — not raw text, but clean, harmonized data.

A critical aspect of this challenge is not only extracting data, but ensuring that results are verifiable and trustworthy.

---

**🟢 YOUR TASK**

Build an AI system that answers natural language clinical questions by extracting and presenting structured, source-grounded data from full-text scientific papers (PDFs).

**Your system should:**
• take a natural language query as input
• retrieve relevant information from a small collection of clinical papers
• extract key data into structured fields
• return a clean, analysis-ready evidence table (not just text)
• link every extracted value to its exact source in the paper
• include mechanisms to verify that extracted values are explicitly supported by the source text

---

**🧩 EXAMPLE OF PIPELINE**

This example illustrates one possible approach.
Participants are encouraged to adapt, simplify, or redesign the pipeline based on their ideas.

**1. DOCUMENT INGESTION**
• parsing PDF files into text
• optionally extracting tables and figures

**2. DOCUMENT SEGMENTATION**
• splitting documents into manageable passages or sections

**3. RETRIEVAL**
• identifying document fragments relevant to a given query

**4. STRUCTURED EXTRACTION**
• extracting key fields into a structured format (e.g., JSON) using LLMs or other methods

**5. VALIDATION**
• checking that extracted values are supported by the source text
• handling missing or uncertain information explicitly

**6. DATA AGGREGATION**
• combining extracted information across multiple studies into a structured table

**7. RESULT PRESENTATION**
• displaying the structured results
• optionally providing a short summary or explanation

---

**🟢 MINIMAL REQUIREMENTS**

Your system must:

• **INPUT:** Accept 20–30 scientific articles (PDFs)
  *(optional: extend with additional public sources)*

• **INTERFACE:** Participants may use any lightweight interface
  *(chat UI, web app, notebook, or other simple format)*

• **PROCESSING:** Retrieve relevant parts of documents and extract a consistent set of fields using LLM or other methods

• **OUTPUT:** Return a structured table *(not just text)*

• **TRACEABILITY:** Link extracted values to source text

• **SAFETY:** Clearly indicate missing information ("not reported") and avoid unsupported claims

• The solution must solve at least one use case:
  **COUNTERFACTUAL MORTALITY ESTIMATION**
  *(extra points for covering additional use cases)*

---

**⏱️ NOTES**

Given the limited time (2 days), focus on a **working end-to-end prototype** rather than full completeness.

It is acceptable to:
• work with a subset of documents (e.g., 5–10 papers)
• simplify validation mechanisms
• focus on a limited set of fields or variables
• partially implement the pipeline

Completeness and clarity of the solution are more important than covering all possible cases.

**🧩 DATA EXTENSION (OPTIONAL)**

Participants may extend the provided dataset by incorporating additional publicly available scientific articles *(e.g., PubMed, open-access sources).*

Such extensions are optional but may improve:
• coverage of evidence
• robustness of results
• generalization across studies

All externally added data must be processed using the same pipeline and adhere to the same requirements for traceability and validation.

---

**🟢 USE CASE 1: COUNTERFACTUAL MORTALITY ESTIMATION**

**Context**
A clinical registry contains patients with sepsis treated using hemoadsorption. The dataset includes:
• demographics
• diagnoses
• laboratory values
• clinical parameters over time
• patient outcomes

However, the registry does not include a control group.

**Objective**
Estimate expected mortality for similar patients **in the absence of treatment**, using evidence from published studies.

**Role of the AI System**
The system is not expected to directly compute mortality.
Instead, it should extract from literature:
• associations between clinical variables and mortality
• prognostic biomarkers *(e.g., lactate, IL-6, lymphocytes)*
• severity scores *(e.g., SOFA, APACHE)*
• statistical modeling approaches
• cohort characteristics

**Expected Output**
A structured evidence table that includes:
• predictor variables
• outcome definitions *(e.g., 28-day mortality)*
• effect sizes *(AUC, OR, HR)*
• statistical methods
• cohort descriptions
• source anchors

```
Study: Leona 2025
Population: Abdominal sepsis after surgery (Japan)
Sample Size: N=147 ED; N=238 ICU; N=123 survivors
Predictor: Lymphocyte count
Outcome: 28-day mortality
Timing: First 24h after diagnosis
Method: ROC analysis
Effect Size: Cutoff 0.8×10^9
Performance: AUC 0.78 (95% CI 0.72–0.93), Sens 0.9, Spec 0.8, p<0.001
Notes: Youden rule used
Source: Results section (p.X)

----------------------------------------

Study: Michelangelo 2024
Population: ED patients with suspected infection (USA)
Sample Size: N=341 ED; N=41 ICU; N=200
Predictor: Lymphocyte count (log-transformed)
Outcome: In-hospital mortality
Timing: Within first admission
Method: Logistic regression (univariable + multivariable)
Effect Size: OR 1.2 (95% CI 1.02–1.4)
Performance: AUC 0.78 (95% CI 0.73–0.84)
Notes: Adjusted for SOFA and age
Source: Table / Results (p.Y)

----------------------------------------
```

**Outcome:**
This enables downstream statistical modeling and estimation by domain experts.
