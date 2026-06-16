"""Azure Functions app entrypoint.

This folder is intentionally self-contained and does not import from
ResumeRankerMCP/common or ResumeRankerFrontend.
"""

import importlib

from resume_trigger import resume_blob_trigger
from jd_trigger import jd_blob_trigger

try:
    func = importlib.import_module("azure.functions")
except ImportError:  # pragma: no cover - local editing fallback
    func = None

if func is None:
    raise RuntimeError(
        "azure-functions is required to run ResumeRankerFunctions. "
        "Install with: pip install azure-functions"
    )

app = func.FunctionApp()


@app.function_name(name="resume_blob_trigger")
@app.blob_trigger(arg_name="blob", path="resumes/{name}", connection="AZURE_STORAGE_CONNECTION_STRING")
def resume_blob_handler(blob):
    resume_blob_trigger(blob)


@app.function_name(name="jd_blob_trigger")
@app.blob_trigger(arg_name="blob", path="jds/{name}", connection="AZURE_STORAGE_CONNECTION_STRING")
def jd_blob_handler(blob):
    jd_blob_trigger(blob)
