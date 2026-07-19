#!/bin/bash
# ZenGoal — deploy su Cloud Run (eseguire sull'host, richiede CLOUDSDK_AUTH_ACCESS_TOKEN)
set -e
cd /home/digitalvisions/projects/zengoal
exec ~/google-cloud-sdk/bin/gcloud run deploy zengoal \
  --source . \
  --project winged-citron-349012 \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY},GEMINI_MODEL=gemini-3-flash-preview" \
  --quiet
