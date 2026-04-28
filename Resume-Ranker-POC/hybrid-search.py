
import os
from azure.storage.blob import BlobServiceClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI

# ------------------ Configuration ------------------
# Azure Blob
CONNECTION_STRING = "REPLACE_WITH_YOUR_CONNECTION_STRING_VALUE_HERE"
container_name = "resumes"  # Make sure this is your resume blob container
jd_container = "jds"
jd_blob = "Job Listing Detail_fin.txt"

# Azure AI Search
search_service_endpoint = "REPLACE_WITH_YOUR_SEARCH_SERVICE_ENDPOINT_VALUE_HERE"
index_name = "REPLACE_WITH_YOUR_INDEX_NAME_VALUE_HERE"
query_api_key = "REPLACE_WITH_YOUR_QUERY_API_KEY_VALUE_HERE"

# Azure OpenAI
openai_endpoint = "REPLACE_WITH_YOUR_OPENAI_ENDPOINT_VALUE_HERE"
deployment = "REPLACE_WITH_YOUR_DEPLOYMENT_NAME_VALUE_HERE"
subscription_key = "REPLACE_WITH_YOUR_OPENAI_API_KEY_VALUE_HERE"

# ------------------ Initialize Clients ------------------
# Blob clients
blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
jd_blob_client = blob_service_client.get_blob_client(container=jd_container, blob=jd_blob)

# AI Search client
search_client = SearchClient(endpoint=search_service_endpoint,
                             index_name=index_name,
                             credential=AzureKeyCredential(query_api_key))

# OpenAI client
openai_client = AzureOpenAI(
    azure_endpoint=openai_endpoint,
    api_key=subscription_key,
    api_version="2025-01-01-preview"
)

# ------------------ Step 1: Fetch JD ------------------
jd_text = jd_blob_client.download_blob().readall().decode('utf-8')

# ------------------ Step 2: Search Resumes ------------------
search_results = search_client.search(search_text=jd_text, top=5)

# ------------------ Step 3: Evaluate Resumes ------------------
# Fetch all search results into a list to avoid iterator issues
results = list(search_results)

print(f"\nFound {len(results)} resume(s) to evaluate.\n")

for i, result in enumerate(results):
    try:
        resume_blob_name = result['metadata_storage_name']
        score_from_search = result['@search.score']
        
        # Fetch resume content from blob
        resume_blob_client = blob_service_client.get_blob_client(container=container_name, blob=resume_blob_name)
        resume_text = resume_blob_client.download_blob().readall().decode('utf-8')

        # Prepare OpenAI chat prompt
        chat_prompt = [
            {"role": "system", "content": "You are an expert in evaluating resumes against job descriptions (JD) using ATS criteria."},
            {"role": "user", "content": jd_text},
            {"role": "user", "content": "I need you to understand the Job description attached in detail and pick out keywords, important points so that we can compare with resumes later."},
            {"role": "user", "content": resume_text},
            {"role": "user", "content": """Evaluation Criteria:
1. Keywords:
  - Skills
  - Job Titles
2. Experience:
  - Relevant Experience
  - Quantifiable Achievements
3. Education:
  - Degree Requirements
  - Section Headings
5. Soft Skills:
  - Interpersonal Skills
  - Cultural Fit
6. Certifications and Training:
  - Relevant Certifications
  - Professional Development

Please provide individual scores for each category on a scale of 1 to 100, with 100 being the highest. Then, calculate and provide a combined overall score."""},
            {"role": "user", "content": "Respond with each score in this exact format:\nKeywords: [number]\nExperience: [number]\nEducation: [number]\nSoft Skills: [number]\nCertifications and Training: [number]\nOverall Score: [number]"}
        ]

        # Call OpenAI
        completion = openai_client.chat.completions.create(
            model=deployment,
            messages=chat_prompt,
            max_tokens=200,
            temperature=0.7,
            top_p=0.95,
            frequency_penalty=0,
            presence_penalty=0
        )

        openai_score = completion.choices[0].message.content.strip()
        lines = openai_score.splitlines()
        scores = {}

        # Parse OpenAI response and extract scores
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                try:
                    scores[key] = int(value.strip())
                except ValueError:
                    scores[key] = value.strip()

        # Print results
        print(f"\n{'='*50}")
        print(f"Resume {i+1}: {resume_blob_name}")
        print(f"AI Search Score: {score_from_search:.2f}")
        print(f"OpenAI Match Score Breakdown:")
        print(f"  1. Keywords: {scores.get('Keywords', 0)}")
        print(f"  2. Experience: {scores.get('Experience', 0)}")
        print(f"  3. Education: {scores.get('Education', 0)}")
        print(f"  4. Soft Skills: {scores.get('Soft Skills', 0)}")
        print(f"  5. Certifications and Training: {scores.get('Certifications and Training', 0)}")
        print(f"Overall Score: {scores.get('Overall Score', 0)}/100")
        print(f"{'='*50}\n")

    except Exception as e:
        print(f"⚠️ Error evaluating resume {i+1} ({resume_blob_name}): {e}\n")
