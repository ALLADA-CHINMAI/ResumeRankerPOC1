# Resume Ranker POC

An AI-powered resume screening tool. Give it a job description and it ranks all resumes in your Azure Blob Storage by match score.

---

## What it does

1. Reads a Job Description (JD) from Azure Blob Storage
2. Uses Azure OpenAI (GPT-4o) to extract the key skills, titles, and requirements from the JD
3. Uses Azure AI Search to retrieve the top 30 most relevant resume chunks
4. Scores each resume with GPT-4o on a 0–100 scale
5. Combines both scores and displays a ranked table in the Streamlit UI

**Final score formula:** `0.9 × GPT_Score + 0.1 × AI_Search_Score`

---

## Architecture

```
Azure Blob Storage
├── jds/             ← Job Description files (.txt, .pdf, .docx)
└── resumes/         ← Resume files (.txt, .pdf, .docx)
        │
        ▼
Azure AI Search (Data Source → Skillset → Index → Indexer)
        │   Skillset: splits documents into chunks, embeds each chunk via Azure OpenAI
        ▼
Streamlit App  ──►  Azure OpenAI (GPT-4o)
        │
        ▼
Ranked results table
```

---

## Chunking, Embedding, Indexing, and Search — what happens where

| Step | Done by | Where |
|---|---|---|
| **Chunking** | Azure AI Search indexer skillset (SplitSkill) | Splits each resume into 2000-char chunks with 200-char overlap |
| **Embedding** | Azure AI Search indexer skillset (AzureOpenAIEmbeddingSkill) | Each chunk is embedded using `text-embedding-ada-002` |
| **Indexing** | Azure AI Search indexer | Chunks + vectors stored in the search index. Indexer runs on upload or on demand |
| **Search** | Azure AI Search (`SearchClient`) | Text search using GPT-extracted JD keywords. Returns top 30 matching chunks |
| **AI Scoring** | Azure OpenAI GPT-4o | Each returned chunk is scored 0–100 based on experience, skills, education, and keywords |

> The app does **not** do its own chunking or embedding. Both are delegated to the Azure AI Search indexer.

---

## Azure Portal Setup (step-by-step)

Do this once before running the app. You need an Azure subscription.

---

### Step 1 — Create a Storage Account

1. Go to [portal.azure.com](https://portal.azure.com) → **Create a resource** → search **Storage account** → **Create**
2. Fill in:
   - **Subscription / Resource group** — pick or create one
   - **Storage account name** — e.g. `resumerankerstorage` (must be globally unique, lowercase, no hyphens)
   - **Region** — pick the same region as your OpenAI and Search services
   - **Performance** — Standard
   - **Redundancy** — LRS (sufficient for a POC)
3. Click **Review + create** → **Create** and wait for deployment
4. Go to the resource → left menu → **Access keys** → copy the **Connection string** for **key1** — you will need this for `.env`

---

### Step 2 — Create Blob Containers

Still inside your storage account:

1. Left menu → **Containers** → **+ Container**
   - Name: `resumes` | Access level: **Private** → **Create**
2. **+ Container** again
   - Name: `jds` | Access level: **Private** → **Create**

---

### Step 3 — Create an Azure AI Search Service

1. **Create a resource** → search **Azure AI Search** → **Create**
2. Fill in:
   - **Service name** — e.g. `resume-ranker-search` (globally unique)
   - **Location** — same region as storage and OpenAI
   - **Pricing tier** — **Basic** or higher (Free tier does not support vector search)
3. **Review + create** → **Create**
4. After deployment → go to the resource:
   - Note the **URL** from the Overview page (e.g. `https://resume-ranker-search.search.windows.net`)
   - Left menu → **Keys** → copy the **Primary admin key** — you will need this for `.env`

---

### Step 4 — Create the Index, Skillset, Indexer (Import and Vectorize Data wizard)

This wizard creates all four Azure AI Search components (data source, index, skillset, indexer) in one flow.

1. Go to your **Azure AI Search** service → click **Import and vectorize data** (top of Overview page)

2. **Connect to your data**
   - Data source: **Azure Blob Storage**
   - Subscription: your subscription
   - Storage account: the one you created in Step 1
   - Blob container: `resumes`
   - Click **Next**

3. **Vectorize your text**
   - Kind: **Azure OpenAI**
   - Subscription: your subscription
   - Azure OpenAI service: your OpenAI resource
   - Model deployment: `text-embedding-ada-002` (must already exist in your OpenAI resource)
   - Authentication type: **API key**
   - Acknowledge the usage charges checkbox
   - Click **Next**

4. **Vectorize and enrich images** — leave off, click **Next**

5. **Advanced settings**
   - Check **Enable semantic ranking** if your tier supports it (optional)
   - Check **Schedule indexer** → **Once** for now (you can run manually later)
   - Click **Next**

6. **Review and create**
   - **Object name prefix** — type `resume-ranker` (the wizard will name the index `resume-ranker-index`, indexer `resume-ranker-indexer`, etc.)
   - Click **Create**

7. Wait for the wizard to finish creating all resources. You will see green checkmarks for: data source, skillset, index, indexer.

8. **Check the indexer ran** — left menu → **Indexers** → click `resume-ranker-indexer` → confirm Status is **Success** (it may take a few minutes on the first run)

> **Note on field names:** The wizard creates the index with fields `chunk_id`, `parent_id`, `title`, `chunk`, and `text_vector`. The app code references exactly these names.

---

### Step 5 — Verify the Index Fields

1. Left menu → **Indexes** → click `resume-ranker-index` → **Fields** tab
2. Confirm these fields exist:

   | Field name | Type | Key | Searchable | Retrievable |
   |---|---|---|---|---|
   | `chunk_id` | Edm.String | Yes | — | Yes |
   | `parent_id` | Edm.String | — | — | Yes |
   | `title` | Edm.String | — | Yes | Yes |
   | `chunk` | Edm.String | — | Yes | Yes |
   | `text_vector` | Collection(Edm.Single) | — | Yes | — |

   If any field is missing or named differently, you will need to edit the index JSON (Index → **Edit JSON**) to match the table above.

---

### Step 6 — Get the Indexer Name

1. Left menu → **Indexers** → note the exact name of the indexer (e.g. `resume-ranker-indexer`)
2. You will put this in `.env` as `SEARCH_INDEXER_NAME`

---

### Step 7 — Configure the `.env` file

Create a file named `.env` in the project root (copy from `.env.example`):

```powershell
Copy-Item .env.example .env
```

Then open `.env` and fill in all values:

```
# Azure OpenAI
OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
OPENAI_DEPLOYMENT_NAME=gpt-4o
OPENAI_API_KEY=<your OpenAI API key>
EMBEDINGS_OPENAI_DEPLOYMENT_NAME=text-embedding-ada-002

# Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING=<connection string from Step 1>
RESUME_CONTAINER_NAME=resumes
JD_CONTAINER_NAME=jds

# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<your-service>.search.windows.net
AZURE_SEARCH_API_KEY=<admin key from Step 3>
SEARCH_INDEX_NAME=resume-ranker-index
SEARCH_INDEXER_NAME=resume-ranker-indexer
SEARCH_DATASOURCE_NAME=resume-ranker-datasource
SEARCH_SKILLSET_NAME=resume-ranker-skillset
```

Where to find each value:

| Variable | Where in Azure Portal |
|---|---|
| `OPENAI_ENDPOINT` | OpenAI resource → Overview → Endpoint |
| `OPENAI_API_KEY` | OpenAI resource → Keys and Endpoint → KEY 1 |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage account → Access keys → Connection string (key1) |
| `AZURE_SEARCH_ENDPOINT` | AI Search service → Overview → Url |
| `AZURE_SEARCH_API_KEY` | AI Search service → Keys → Primary admin key |
| `SEARCH_INDEXER_NAME` | AI Search service → Indexers → (exact name shown) |

---

### Step 8 — Install Python dependencies

```powershell
pip install -r requirements.txt
```

---

### Step 9 — Upload sample data

Upload the included sample resumes and JDs to Blob Storage:

```powershell
$env:AZURE_STORAGE_CONNECTION_STRING="<your connection string>"
python Resume-Ranker-POC\upload_blobs.py
```

After uploading, trigger the indexer from the portal:
- **AI Search service** → **Indexers** → `resume-ranker-indexer` → **Run**
- Wait for status to show **Success**

---

### Step 10 — Run the app

```powershell
streamlit run Resume-Ranker-POC\Demo\app.py
```

Open the local Streamlit URL shown in the terminal. Select a JD from the dropdown and click **Rank Resumes**.

---

## Troubleshooting

**Indexer shows "Transient failure" on embedding skill**
- The Search service needs permission to call your Azure OpenAI resource. Go to your OpenAI resource → **Access control (IAM)** → **Add role assignment** → role: **Cognitive Services OpenAI User** → assign to your Search service's managed identity.

**`chunk` or `title` field not found in search results**
- The index field names do not match. Open the index in the portal → **Edit JSON** and verify `chunk` and `title` fields exist and are marked `retrievable: true`.

**Indexer never triggers after file upload**
- The app calls `run_indexer()` immediately after upload. If the indexer is already running, this call is a no-op. Wait for the current run to finish, then manually trigger from the portal or click **Run** in the Indexers panel.

**Empty results table**
- Check that the indexer ran successfully and the document count in the index is > 0 (Overview → index → Document count).

---

## Project structure

```
ResumeRankerPOC1/
├── .env.example                            ← Copy to .env and fill in values
├── setup_azure.py                          ← (Optional) script alternative to portal setup
├── Resume-Ranker-POC/
│   ├── Demo/
│   │   └── app.py                          ← Main Streamlit app
│   ├── jds/                               ← Sample job description files
│   ├── resumes/                           ← Sample resume files
│   ├── upload_blobs.py                    ← One-time upload utility
│   ├── fetch-AI-search_integrated2.py     ← CLI version with per-category scoring
│   └── hybrid-search.py                  ← Hybrid text + vector search experiment
├── testing text extractor.py             ← Utility: extract text from PDF/DOCX
├── requirements.txt
└── README.md
```
