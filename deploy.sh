#!/usr/bin/env bash
# Deploy ssa-landing Cloud Run service (apex + www domains).
# Since 2026-07-30 this is a FastAPI app (auth + billing hub), not static nginx.
# Builds + pushes image to us-east1 Artifact Registry, deploys to us-east1.
# IMPORTANT: --region=us-east1 must be explicit. See ANALYTICS_PROJECT_GUIDELINES.md.
#
#   ./deploy.sh            — deploy + validate + health check
#   ./deploy.sh bootstrap  — PRINT the one-time setup steps for the paywall
#                            (Firestore, IAM, Firebase domains, Stripe). Nothing
#                            is executed; run the steps deliberately.

set -euo pipefail

PROJECT=golf-data-projects
REGION=us-east1
SERVICE=ssa-landing
AUTH_PROJECT=ssa-auth-71d16

if [[ "${1:-}" == "bootstrap" ]]; then
  cat <<'EOF'
── One-time paywall bootstrap (run each step deliberately) ─────────────────────

1. Firestore for entitlements (shared auth project, us-east1):
   gcloud firestore databases create --project=ssa-auth-71d16 --location=us-east1

2. Runtime SA needs Firestore access on ssa-auth-71d16, token mint on itself,
   AND session-cookie minting rights (the 2026-07-31 field lesson: without the
   last two lines, POST /api/session 500s and sign-in looks fine client-side
   while every league site stays signed out):
   SA=$(gcloud run services describe ssa-landing --region=us-east1 \
        --project=golf-data-projects \
        --format='value(spec.template.spec.serviceAccountName)')
   gcloud projects add-iam-policy-binding ssa-auth-71d16 \
     --member="serviceAccount:${SA}" --role="roles/datastore.user"
   gcloud iam service-accounts add-iam-policy-binding "${SA}" \
     --member="serviceAccount:${SA}" \
     --role="roles/iam.serviceAccountTokenCreator" --project=golf-data-projects
   # create_session_cookie calls the Identity Toolkit API: it must be ENABLED
   # on the CALLING project, and the SA needs firebaseauth.admin on the auth
   # project (mirrors what every league runtime SA already has):
   gcloud services enable identitytoolkit.googleapis.com --project=golf-data-projects
   gcloud projects add-iam-policy-binding ssa-auth-71d16 \
     --member="serviceAccount:${SA}" --role="roles/firebaseauth.admin"

3. Firebase (Console/API, per soccer-hub CLAUDE.md gotchas):
   - authorizedDomains += sportsbookscienceanalytics.com AND www.… (GET current
     list first, PATCH the MERGED list — it's a full replace).
   - OAuth Web client (ssa-auth-71d16) Authorized redirect URIs +=
     https://sportsbookscienceanalytics.com/__/auth/handler
   - Set FIREBASE_API_KEY / FIREBASE_APP_ID envs on the service (copy from any
     league service: gcloud run services describe cfl-dashboard …)

4. Service env vars (first deploy only — they persist afterwards):
   ⚠ ALWAYS --update-env-vars, NEVER --set-env-vars. --set-env-vars REPLACES the
     whole env set, so step 5 below would delete everything this step just wrote
     and customer sign-in would break with no error anywhere.
   gcloud run services update ssa-landing --region=us-east1 --project=golf-data-projects \
     --update-env-vars=FIREBASE_PROJECT_ID=ssa-auth-71d16,FIREBASE_AUTH_DOMAIN=sportsbookscienceanalytics.com,FIREBASE_API_KEY=…,FIREBASE_APP_ID=…,FIRESTORE_PROJECT_ID=ssa-auth-71d16,PUBLIC_BASE_URL=https://sportsbookscienceanalytics.com

5. Stripe (test mode first):
   - Create 6 Products with monthly Prices (SKUs: sport_cfl, sport_ncaaf,
     sport_nfl, sport_golf, bundle_football, all_access).
   - Secret Manager: stripe-secret-key + stripe-webhook-secret in golf-data-projects,
     then bind:
     ⚠ --update-env-vars here too (see step 4) or this WIPES the Firebase vars.
     gcloud run services update ssa-landing --region=us-east1 --project=golf-data-projects \
       --set-secrets=STRIPE_SECRET_KEY=stripe-secret-key:latest,STRIPE_WEBHOOK_SECRET=stripe-webhook-secret:latest \
       --update-env-vars=STRIPE_PRICE_SPORT_CFL=price_…,STRIPE_PRICE_SPORT_NCAAF=price_…,STRIPE_PRICE_SPORT_NFL=price_…,STRIPE_PRICE_SPORT_GOLF=price_…,STRIPE_PRICE_BUNDLE_FOOTBALL=price_…,STRIPE_PRICE_ALL_ACCESS=price_…
   - Also set the DISPLAY prices, or /pricing advertises the $9.99 placeholder
     while Stripe charges the real amount:
       --update-env-vars=PRICE_DISPLAY_SPORT_CFL=<cents>,… (one per sellable SKU)
   - CHANGING A PRICE LATER: Stripe Prices are immutable, so a new amount means a
     NEW price id while existing subscriptions keep the old one. PREPEND the new
     id, keep the old — STRIPE_PRICE_<SKU> accepts a comma-separated list and the
     first entry is what new checkouts use:
       --update-env-vars=STRIPE_PRICE_SPORT_NCAAF=price_NEW,price_OLD
     Dropping the old id makes the next webhook for a legacy customer refuse to
     write (500, Stripe retries) rather than silently revoking them.
   - Friend/discount codes: use Stripe COUPONS + promotion codes, never a second
     Price. allow_promotion_codes is already on in create_checkout_session, and a
     coupon keeps the same price id so none of the above applies.
   - Stripe webhook endpoint: https://sportsbookscienceanalytics.com/api/billing/webhook
     events: checkout.session.completed, customer.subscription.created/updated/deleted
EOF
  exit 0
fi

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
echo "── Config sanity (warn-only) ──"
ENVS=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --project="${PROJECT}" \
  --format='value(spec.template.spec.containers[0].env)')
# The STRIPE_PRICE_* vars are checked too: a --set-env-vars anywhere in the
# runbook drops them, and the next webhook per customer then hits an
# unresolvable price. That now refuses to write (500 + Stripe retry) instead of
# silently revoking, but the deploy should still say so out loud.
for key in FIREBASE_API_KEY FIREBASE_PROJECT_ID FIREBASE_AUTH_DOMAIN FIREBASE_APP_ID \
           FIRESTORE_PROJECT_ID PUBLIC_BASE_URL STRIPE_SECRET_KEY \
           STRIPE_PRICE_SPORT_CFL STRIPE_PRICE_SPORT_NCAAF STRIPE_PRICE_SPORT_NFL \
           STRIPE_PRICE_SPORT_GOLF STRIPE_PRICE_BUNDLE_FOOTBALL STRIPE_PRICE_ALL_ACCESS; do
  if [[ "${ENVS}" != *"${key}"* ]]; then
    echo "  ⚠ ${key} not set on the service — sign-in/billing degraded. See ./deploy.sh bootstrap"
  fi
done

echo
echo "── Health check (apex + www + API) ──"
for url in "https://sportsbookscienceanalytics.com" "https://www.sportsbookscienceanalytics.com" "https://sportsbookscienceanalytics.com/api/health"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "${url}")
  echo "  ${url} → HTTP ${CODE}"
done
