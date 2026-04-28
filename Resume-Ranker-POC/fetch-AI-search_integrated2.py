#FETCH A JD FROM AZURE BLOB STORAGE AND SEARCH RESUMES USING THAT JD AND INDEX THEM BASED ON MATCHING SCORE-----------------------------

import os
import json
from azure.storage.blob import BlobServiceClient
from tabulate import tabulate
#AI-search---------------------------------------------------------
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexerClient
from azure.core.credentials import AzureKeyCredential

#AI-Scoring---------------------------------------------------------
import base64
from openai import AzureOpenAI  

#Configuring Model--------------------------------------------------
endpoint = os.getenv("ENDPOINT_URL", "REPLACE_WITH_YOUR_ENDPOINT_URL_VALUE_HERE")  
deployment = os.getenv("DEPLOYMENT_NAME", "REPLACE_WITH_YOUR_DEPLOYMENT_NAME_VALUE_HERE")  
subscription_key = os.getenv("OPENAI_API_KEYa", "REPLACE_WITH_YOUR_API_KEY_VALUE_HERE")  

# Initialize Azure OpenAI Service client with key-based authentication    
client = AzureOpenAI(  
    azure_endpoint=endpoint,  
    api_key=subscription_key,  
    api_version="2025-01-01-preview",
)

#Fetch--------------------------------------------------------
# Replace with your actual connection string of storage account
CONNECTION_STRING = os.getenv("CONNECTION_STRING", "REPLACE_WITH_YOUR_CONNECTION_STRING_VALUE_HERE")

# Initialize the BlobServiceClient
blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)

# Specify the container and blob (JD file) names
container_name = "jds"
blob_name = "Job Listing Detail_sn.txt"  # Replace with your actual JD file name

#AI-search--------------------------------------------------------------------------------------------

search_service_endpoint = os.getenv("SEARCH_SERVICE_ENDPOINT", "REPLACE_WITH_YOUR_SEARCH_SERVICE_ENDPOINT_VALUE_HERE")
index_name = os.getenv("INDEX_NAME", "REPLACE_WITH_YOUR_INDEX_NAME_VALUE_HERE")
query_api_key = os.getenv("QUERY_API_KEY", "REPLACE_WITH_YOUR_QUERY_API_KEY_VALUE_HERE")


# Initialize the SearchClient
search_client = SearchClient(endpoint=search_service_endpoint,
                             index_name=index_name,
                             credential=AzureKeyCredential(query_api_key))

# Run the Vectorisation indexer
#Fetch--------------------------------------------------------------------------------------------
# Get the BlobClient
blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

# Download and read the blob contentj
download_stream = blob_client.download_blob()
jd_text = download_stream.readall().decode('utf-8')

# print("Job Description Content:")
# print(jd_text)

# Keywords and important details Extraction----------------------------------------------------------------------------
def MainContent(jd_content):
    print("Extracting main content from JD...")


# #Prepare the chat prompt 
    chat_prompt = [
        {"role": "system", "content": """You are an AI assistant that extract important keywords and details from a resume like .
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
         """},
    {
        "role": "user",
        "content":f" <<JD>>${jd_content}<<Jd>>"
    },
    {
        "role": "user",
        "content":f"it is being used for vector search so give text accordingly, "
    }
]
    
    # Include speech result if speech is enabled  
    messages = chat_prompt  
        
    # Generate the completion  
    completion = client.chat.completions.create(  
        model=deployment,
        messages=messages,
        max_tokens=1500,  
        temperature=0.7,  
        top_p=0.95,  
        frequency_penalty=0,  
        presence_penalty=0,
        stop=None,  
        stream=False
    )
    return completion.choices[0].message.content


#AI-search---------------------------------------------------------------
# Perform the search
mainContentOfJD = MainContent(jd_text)
# print("main content\n", mainContentOfJD)
results = search_client.search(search_text=mainContentOfJD, top=30)
# what type of search?? vectorized, 
#AI ScoreFunction--------------------------------------------------------
def AIScore(jd_content, resume_content):

# #Prepare the chat prompt 
    chat_prompt = [
    {
        "role": "system",
        "content": "You are an AI assistant that evaluates resumes based on job descriptions. You must score resumes strictly based on the content provided, without making assumptions. Understand the semantics of the text, not just keyword matches."
    },
    {
        "role": "user",
        "content": """Evaluate the following resume against the job description using the following weighted criteria and rules:

        Scoring Criteria and Weights:
        - Experience (20 points): Score 0 if experience is less than required.
        - Primary Skills (20 points): Match exact and semantically related skills.
        - Secondary Skills (15 points): Match related or complementary skills.
        - PGC Competitor Experience (10 points): Check for experience in healthcare or companies like Health Engineers.
        - Location (10 points): Match with job location or mention of relocation.
        - Certification (10 points): Relevant and recognized certifications.
        - Education (5 points): Relevance of top two degrees.

        Scoring Rules:
        - Do not assume anything not explicitly mentioned in the resume.
        - Understand the meaning of words, not just exact matches.
        - If the content is not a resume or relevant experience is less, return totalScore: -2.
        - Exceptional skills should stand out.

        Return the result in the following dictionaryString format:
        `{
        "totalScore": NN.NN,
        "scores": {
            "experience": NN,
            "primarySkills": NN,
            "secondarySkills": NN,
            "pgcCompetitors": NN,
            "location": NN,
            "certification": NN,
            "education": NN
        },
        "scoringReasons": {
            "experience": "...",
            "primarySkills": "...",
            "secondarySkills": "...",
            "pgcCompetitors": "...",
            "location": "...",
            "certification": "...",
            "education": "..."
        },
        "explanation": "Your detailed explanation of the scoring."
        }`"""
    },
    {
        "role": "user",
        "content":f"Job Description:<<JD>> ${jd_content} <<JD>>, Resume:<<Resume>> ${resume_content} <<Resume>>"
    }
    ]
    # Include speech result if speech is enabled
    messages = chat_prompt
    # print("messages:\n", messages)
    # Generate the completion
    completion = client.chat.completions.create(
        model=deployment,
        messages=messages,
        max_tokens=1500,  
        temperature=0.7,  
        top_p=0.95,  
        frequency_penalty=0,  
        presence_penalty=0,
        stop=None,  
        stream=False
    )
    # return 1;
    return completion.choices[0].message.content

print("results are as below")
scoresList = []
aiScore = -1
result = next(results)  # Get the first result from the search results
while(aiScore == -1):
    result_ = AIScore(jd_text,result['chunk'])
    try:
        cleaned_json_string = result_.strip('`json\n').strip('`')
        result_ = json.loads(cleaned_json_string);
        aiScore=result_['totalScore']
        aiScore = float(aiScore)
        scores = result_['scores']
        scoreReasons = result_['scoringReasons']
    except ValueError:
        aiScore = -1
scoresList.append([result['title'],result['@search.score'], aiScore, result['chunk'], result_['scores'], result_['scoringReasons']])

# for result in results:
#     aiScore = -1
#     while(aiScore == -1):
#         result_ = AIScore(jd_text,result['chunk'])
#         #  json string to python dict
#         try:
#             # if result has json in front of it, remove it
#             # if result_.startswith("json"):
#             #     result_ = result_[4:]
#             res = result_.lstrip("json").strip()
#             print("result_:\n", res)
#             result_ = json.loads(result_)
#         except json.JSONDecodeError:
#             print("Failed to decode JSON")
#             continue
#         try:
#             print("result['chunk']:\n", result['chunk'])
#             print("AI Score: ", aiScore)
#             aiScore=result_['totalScore']
#             aiScore = float(aiScore)
#         except ValueError:
#             aiScore = -1
#     # print(f"Resume ID: {result['metadata_storage_name']}, Score: {result['@search.score']}")
#     # print(f"Resume ID: {result['title']}, Score: {result['@search.score']}, AIScore: {aiScore}")
#     scoresList.append([result['title'],result['@search.score'], aiScore, result['chunk'], result_['scores']])
#     break
# print("scoresList:\n", scoresList)
#printing in tabular format
# 
# print(tabulate(scoresList, headers=['Name', 'AiSearchScore', 'AiGptScore'], tablefmt='grid'))

# print(scoresList)

# create sorted list wrt to both names
newList=[]
for score in scoresList:
    # print(score)
    newList.append([score[0],float(score[2])*0.9+float(score[1])*0.1])
newList = sorted(newList,key = lambda x : -x[1])

# print(newList)
print(tabulate(newList, headers=['Name', 'totalScore'], tablefmt='grid'))

