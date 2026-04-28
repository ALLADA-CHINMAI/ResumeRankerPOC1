#FETCH A JD FROM AZURE BLOB STORAGE AND SEARCH RESUMES USING THAT JD AND INDEX THEM BASED ON MATCHING SCORE-----------------------------

import os
from azure.storage.blob import BlobServiceClient

#AI-search---------------------------------------------------------
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

#Fetch--------------------------------------------------------
# Replace with your actual connection string of storage account
CONNECTION_STRING = os.getenv("CONNECTION_STRING")

# Initialize the BlobServiceClient
blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)

# Specify the container and blob (JD file) names
container_name = "jds"
blob_name = "Job Listing Detail_fin.txt"  # Replace with your actual JD file name

#AI-search--------------------------------------------------------------------------------------------

search_service_endpoint = os.getenv("SEARCH_SERVICE_ENDPOINT")
query_api_key = os.getenv("QUERY_API_KEY")
index_name = "resumes-index"

# Initialize the SearchClient
search_client = SearchClient(endpoint=search_service_endpoint,
                             index_name=index_name,
                             credential=AzureKeyCredential(query_api_key))
#Fetch--------------------------------------------------------------------------------------------
# Get the BlobClient
blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

# Download and read the blob content
download_stream = blob_client.download_blob()
jd_text = download_stream.readall().decode('utf-8')

print("Job Description Content:")
print(jd_text)

#AI-search---------------------------------------------------------------
# Perform the search
results = search_client.search(search_text=jd_text, top=5)

print(results)
# Display the results
for result in results:
    print(f"Resume ID: {result['id']}, Score: {result['@search.score']}")
