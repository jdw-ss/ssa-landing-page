#!/usr/bin/env bash
# Deploy ssa-landing Cloud Run service (apex + www domains).
# Builds + pushes image to us-east1 Artifact Registry, deploys to us-east1 service.
# IMPORTANT: --region=us-east1 must be explicit. See ANALYTICS_PROJECT_GUIDELINES.md.

set -euo pipefail

PROJECT=golf-data-projects
REGION=us-east1
SERVICE=ssa-landing

echo "── Deploying ${SERVICE} from source to ${REGION} ──"
gcloud run deploy "${SERVICE}" \
  --source=. \
  --region="${REGION}" \
  --project="${PROJECT}" \
  --quiet

echo
echo "── Verify image landed in ${REGION} Artifact Registry ──"
IMAGE=$(gcloud run services describe "${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT}" \
  --format='value(spec.template.spec.containers[0].image)')
echo "  image: ${IMAGE}"

if [[ "${IMAGE}" != us-east1-docker.pkg.dev/* ]]; then
  echo "❌ ERROR: image is NOT in us-east1 AR. Check gcloud config."
  exit 1
fi
echo "✓ Image in correct region."

echo
echo "── Health check (apex + www) ──"
for url in "https://sportsbookscienceanalytics.com" "https://www.sportsbookscienceanalytics.com"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "${url}")
  echo "  ${url} → HTTP ${CODE}"
done
