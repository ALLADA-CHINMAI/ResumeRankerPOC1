#!/bin/bash
uvicorn ResumeRankerAPI.main:app --host 0.0.0.0 --port 8000
