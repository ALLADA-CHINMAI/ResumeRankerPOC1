# pip install python-docx
# pip install pdfplumber

# ask the user for a file path 
import os
from docx import Document
import pdfplumber
from typing import Union


def main():
    # with a while loop, ask the user for a file path until a valid file is provided
    # while True:
        # file_path = input("Please enter the path to the document (PDF or DOCX): ").strip()
    file_path = os.getenv("FILE_PATH", "C:\\Users\\raja.pundra\\Downloads\\ServiceNow Profiles\\Chitti Satish.pdf")
    if os.path.isfile(file_path):
        text = extract_text(file_path)
        if text:
            print("Extracted Text:")
            print(text)
            # break
        else:
            print("No text could be extracted from the file. Please try another file.")
    else:
        print("The provided path is not a valid file. Please try again.")
        # continue
    # Uncomment the following line to exit the loop after a successful extraction
    # break
if __name__ == "__main__":
    main()
# This script extracts text from PDF and DOCX files.
# It prompts the user for a file path and continues to ask until a valid file is provided
# and text is successfully extracted.
# The extracted text is printed to the console.
# If the file is not valid or no text can be extracted, it prompts the user again.
