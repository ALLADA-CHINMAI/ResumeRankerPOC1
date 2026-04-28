#COPIED SAME CODE IN FETCH-JDS FILE
import os

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

search_service_endpoint = os.getenv("SEARCH_SERVICE_ENDPOINT", "REPLACE_WITH_YOUR_SEARCH_SERVICE_ENDPOINT_VALUE_HERE")
index_name = os.getenv("INDEX_NAME", "REPLACE_WITH_YOUR_INDEX_NAME_VALUE_HERE")
query_api_key = os.getenv("QUERY_API_KEY", "REPLACE_WITH_YOUR_QUERY_API_KEY_VALUE_HERE")


# Initialize the SearchClient
search_client = SearchClient(endpoint=search_service_endpoint,
                             index_name=index_name,
                             credential=AzureKeyCredential(query_api_key))

# Perform the search
results = search_client.search(search_text=jd_text, top=5)

# Display the results
for result in results:
    print(f"Resume ID: {result['id']}, Score: {result['@search.score']}")
    