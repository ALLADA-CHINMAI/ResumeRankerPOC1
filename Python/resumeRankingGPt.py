 
import os  
import base64
from openai import AzureOpenAI  


endpoint = os.getenv("ENDPOINT_URL", "https://ats.openai.azure.com/")  
deployment = os.getenv("DEPLOYMENT_NAME", "gpt-4o")  
subscription_key = os.getenv("OPENAI_API_KEY", "REPLACE_WITH_YOUR_KEY_VALUE_HERE")  

# Initialize Azure OpenAI Service client with key-based authentication    
client = AzureOpenAI(  
    azure_endpoint=endpoint,  
    api_key=subscription_key,  
    api_version="2025-01-01-preview",
)
    


with open('C:/Users/raja.pundra/source/resumeRanking/JDs/Job Listing Detail_sn.txt', 'r', encoding='utf-8') as file:
    jd_content = file.read()

# print(response.choices[0].message['content'])
with open('C:/Users/raja.pundra/OneDrive - Providence St. Joseph Health/Desktop/template2.html', 'r', encoding='utf-8') as file:
    resume_content = file.read()



# #Prepare the chat prompt 
chat_prompt = [
    # System message to set the context
    {"role": "system", "content": "You are an AI assistant that evaluates resumes based on job descriptions."},

    # User message for evaluating experience
    {"role": "user", "content": "Evaluate the following resume for relevant and irrelevant experience based on the job description provided. Provide a score out of 1000, considering the relevance and weighting criteria.\nResume: [details]\nJob Description: [details]"},

    # User message for assessing skills
    {"role": "user", "content": "Assess the skills listed in the resume against the job description. Score the skills based on exact matches and related skills, considering the weighting criteria.\nResume Skills: [details]\nJob Description Skills: [details]"},

    # User message for evaluating education
    {"role": "user", "content": "Evaluate the educational qualifications in the resume for relevance to the job description. Score the top two degrees based on relevance and weighting criteria.\nResume Education: [details]\nJob Description: [details]"},

    # User message for assessing achievements
    {"role": "user", "content": "Assess the achievements listed in the resume for relevance to the job description. Score the achievements based on relevance and weighting criteria.\nResume Achievements: [details]\nJob Description: [details]"},

    # User message for evaluating clarity and presentation
    {"role": "user", "content": "Evaluate the clarity and presentation of the resume. Provide a minimal score out of 1000, considering the weighting criteria.\nResume: [details]"},

    # User message for identifying and scoring keywords
    {"role": "user", "content": "Identify and score the keywords in the resume that match the job description. Consider the importance and weighting criteria.\nResume: [details]\nJob Description Keywords: [details]"},

    # User message for combining all scores
    {"role": "user", "content": "Combine the scores from experience, skills, education, achievements, clarity and presentation, and keywords to provide a total score out of 1000 for the resume.\nExperience Score: [details]\nSkills Score: [details]\nEducation Score: [details]\nAchievement Score: [details]\nClarity and Presentation Score: [details]\nKeywords Score: [details]"},
    {"role": "user", "content": "dont assume things about not given in resume, only think about things which are there in resume, understand the semantics though"},
    {"role": "user", "content": "if given file content is not resume give -2 score"},

    {
        "role": "user",
        "content":f" <<JD>>${jd_content}<<Jd>> Please review the following file: <<resume>> ${resume_content}<<resume>>"
    },
    {
        "role": "user",
        # "content":f""
        "content":f"just give total score,and also the explanation"

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
print(jd_content,resume_content);
# print(completion.to_json())  
print(completion.choices[0].message.content);  
print("end")
    