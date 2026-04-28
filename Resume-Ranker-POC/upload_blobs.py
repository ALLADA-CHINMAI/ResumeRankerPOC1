import os
from azure.storage.blob import BlobServiceClient

# 1. Read your connection string (used key-1 connection string)
CONNECTION_STRING = os.getenv("CONNECTION_STRING")

# 2. Local directories and their target containers
LOCAL_DIRS = {
    "jds": os.getenv("LOCAL_DIRS_JDS"),
    "resumes": os.getenv("LOCAL_DIRS_RESUMES")
}

# 3. Create the BlobServiceClient
blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)  # :contentReference[oaicite:1]{index=1}

def upload_folder(local_folder: str, container_name: str):
    """
    Uploads all files from local_folder into the specified container.
    """
    container_client = blob_service_client.get_container_client(container_name)
    # Ensure the container exists
    try:
        container_client.create_container()
    except Exception:
        pass  # container already exists

    for filename in os.listdir(local_folder):
        file_path = os.path.join(local_folder, filename)
        if os.path.isfile(file_path):
            blob_client = container_client.get_blob_client(blob=filename)
            with open(file_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)  # :contentReference[oaicite:2]{index=2}
            print(f"Uploaded {filename} to container '{container_name}'")

if __name__ == "__main__":
    for local_folder, container in LOCAL_DIRS.items():
        if os.path.isdir(local_folder):
            upload_folder(local_folder, container)
        else:
            print(f"Local folder '{local_folder}' not found.")
