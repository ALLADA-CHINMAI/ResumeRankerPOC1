#FETCH A JD FROM AZURE BLOB STORAGE AND SEARCH RESUMES USING THAT JD AND INDEX THEM BASED ON MATCHING SCORE-----------------------------

import os
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
endpoint = os.getenv("ENDPOINT_URL", "https://ats.openai.azure.com/")  
deployment = os.getenv("DEPLOYMENT_NAME", "gpt-4o")  
subscription_key = os.getenv("OPENAI_API_KEY", "REPLACE_WITH_YOUR_KEY_VALUE_HERE")  

# Initialize Azure OpenAI Service client with key-based authentication    
client = AzureOpenAI(  
    azure_endpoint=endpoint,  
    api_key=subscription_key,  
    api_version="2025-01-01-preview",
)

#Fetch--------------------------------------------------------
# Replace with your actual connection string of storage account
CONNECTION_STRING = "REPLACE_WITH_YOUR_CONNECTION_STRING_VALUE_HERE"
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
# index_client = SearchIndexerClient(endpoint=search_service_endpoint, credential=AzureKeyCredential("REPLACE_WITH_YOUR_INDEXER_API_KEY_VALUE_HERE"))
# index_client.run_indexer("resumes-vectorised-indexer")
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
        {"role": "system", "content": "You are an AI assistant that evaluates resumes based on job descriptions."},
    
    # User message for evaluating experience
    {"role": "user", "content": "Evaluate the following resume for relevant and irrelevant experience based on the job description provided. Provide a score out of 100, considering the relevance and weighting criteria.\nResume: [details]\nJob Description: [details]"},

    # User message for scoring decription
    {"role": "user", "content": "score in a way that exception skill standout, 0 for no match also see the meaning of the words not only the word."},

    # User message for assessing skills
    {"role": "user", "content": "Assess the skills listed in the resume against the job description. Score the skills based on exact matches and related skills, considering the weighting criteria.\nResume Skills: [details]\nJob Description Skills: [details]"},

    # User message for evaluating education
    {"role": "user", "content": "Evaluate the educational qualifications in the resume for relevance to the job description. Score the top two degrees based on relevance and weighting criteria.\nResume Education: [details]\nJob Description: [details]"},

    # User message for assessing achievements
    {"role": "user", "content": "Assess the achievements listed in the resume for relevance to the job description. Score the achievements based on relevance and weighting criteria.\nResume Achievements: [details]\nJob Description: [details]"},

    # User message for evaluating clarity and presentation
    {"role": "user", "content": "Evaluate the clarity and presentation of the resume. Provide a minimal score out of 100, considering the weighting criteria.\nResume: [details]"},

    # User message for identifying and scoring keywords
    {"role": "user", "content": "Identify and score the keywords in the resume that match the job description. Consider the importance and weighting criteria.\nResume: [details]\nJob Description Keywords: [details]"},

    # User message for combining all scores
    {"role": "user", "content": "Combine the scores from experience, skills, education, achievements, clarity and presentation, and keywords to provide a total score out of 100 for the resume.\nExperience Score: [details]\nSkills Score: [details]\nEducation Score: [details]\nAchievement Score: [details]\nClarity and Presentation Score: [details]\nKeywords Score: [details]"},

    {"role": "user", "content": "dont assume things about not given in resume, only think about things which are there in resume, understand the semantics though"},
    {"role": "user", "content": "if given file content is not resume give -2 score"},

    {
        "role": "user",
        "content":f" <<JD>>${jd_content}<<Jd>> Please review the following file: <<resume>> ${resume_content}<<resume>>"
    },
    {
        "role": "user",
        "content":f"just give total score like NN.NN, dont give any other details"
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

print("results are as below")
scoresList = []
for result in results:
    aiScore = -1
    while(aiScore == -1):
        aiScore = AIScore(jd_text,result['chunk'])
        try:
            aiScore = float(aiScore)
        except ValueError:
            aiScore = -1
    # print(f"Resume ID: {result['metadata_storage_name']}, Score: {result['@search.score']}")
    # print(f"Resume ID: {result['title']}, Score: {result['@search.score']}, AIScore: {aiScore}")
    scoresList.append([result['title'],result['@search.score'], aiScore, result['chunk'] ])


#printing in tabular format
# 
print(tabulate(scoresList, headers=['Name', 'AiSearchScore', 'AiGptScore'], tablefmt='grid'))

# print(scoresList)

# create sorted list wrt to both names
newList=[]
for score in scoresList:
    # print(score)
    newList.append([score[0],float(score[2])*0.9+float(score[1])*0.1])
newList = sorted(newList,key = lambda x : -x[1])

# print(newList)
print(tabulate(newList, headers=['Name', 'totalscore'], tablefmt='grid'))

