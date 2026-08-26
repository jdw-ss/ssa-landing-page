#!/usr/bin/env bash
# Apex front door — the consolidation LB (APEX_CONSOLIDATION_SCOPE.md, Path A,
# John 2026-08-26). Clone of nfl-mock-draft-scraper/infra/frontdoor.sh
# conventions: idempotent stages, run one at a time.
#
#   ./infra/apex-frontdoor.sh backends   # NEG + backend-service per project
#   ./infra/apex-frontdoor.sh urlmap     # host/path routing (DARK until DNS)
#   ./infra/apex-frontdoor.sh dns-auth   # prints the CNAMEs to add at Squarespace
#   ./infra/apex-frontdoor.sh cert       # managed cert via dns-auth (after CNAMEs)
#   ./infra/apex-frontdoor.sh frontend   # IP + proxies + forwarding rules
#   ./infra/apex-frontdoor.sh status
#
# CROSS-PROJECT REFERENCING: the URL map lives in golf-data-projects (with the
# apex + ssa-landing) and references backend services in the league projects by
# full resource path — supported on the global external ALB, same-org. The
# caller needs compute.backendServices.use in each league project (org owner
# has it).
#
# PATH PLAN (initial):
#   default        → ssa-landing-be                    (apex hub, unchanged)
#   /cfl, /cfl/*   → cfl-data-projects/cfl-dash-be     (dual-depth plumbed 08-26)
#   /elomodel/*    → nfl-data-projects/nfl-elo-be      (native prefix; NOT canonical —
#                    kept for the app's own generated redirects, /nfl/elomodel is the URL)
#   /epl/*         → soccer-data-projects/soccer-epl-be (native prefix — zero change)
#   /ncaaf /golf /nhl /nba /soccer → each league's dash backend (dual-depth 08-26)
#   /nfl           → nfl-mockdraft-be (landing + /nfl/mockdrafts); /nfl/elomodel/*
#                    peels to nfl-elo-be on the longer prefix
# Flat /mockdrafts was DROPPED (08-26): that SPA's assets are host-rooted, so the
# flat form on the apex would fetch ssa-landing's statics. /nfl/mockdrafts only.
#
# LEGACY 301 HOST RULES are added at cutover time (stage `redirects`, written
# then), not before: until DNS moves, the old subdomains keep serving normally.

set -euo pipefail
PROJECT=golf-data-projects
REGION=us-east1
G="gcloud --project=${PROJECT}"
URLMAP=apex-frontdoor-urlmap
CERT=apex-frontdoor-cert
CERT_MAP=apex-frontdoor-certmap
IP_NAME=apex-frontdoor-ip
HTTPS_PROXY=apex-frontdoor-https-proxy
HTTP_PROXY=apex-frontdoor-http-proxy
HTTPS_FR=apex-frontdoor-https-fr
HTTP_FR=apex-frontdoor-http-fr
APEX=sportsbookscienceanalytics.com

log() { echo "── $*"; }
ok()  { echo "  ✓ $*"; }
exists() { ${G} "$@" >/dev/null 2>&1; }

be_ref() { echo "projects/$1/global/backendServices/$2"; }

backends() {
  log "Backends live in their own projects (created 2026-08-26; verify only)"
  for pb in golf-data-projects:ssa-landing-be golf-data-projects:golf-dash-be \
            ncaaf-data-projects:ncaaf-dash-be cfl-data-projects:cfl-dash-be \
            nhl-data-projects:nhl-dash-be nba-data-projects:nba-dash-be \
            nfl-data-projects:nfl-elo-be nfl-data-projects:nfl-mockdraft-be \
            soccer-data-projects:soccer-epl-be soccer-data-projects:soccer-hub-be; do
    proj="${pb%%:*}"; be="${pb##*:}"
    if gcloud compute backend-services describe "${be}" --global --project="${proj}" >/dev/null 2>&1; then
      ok "${proj}/${be}"
    else
      echo "  ✗ MISSING ${proj}/${be}"
    fi
  done
}

urlmap() {
  log "URL map (${PROJECT}/${URLMAP}) — DARK until DNS points at the LB"
  if exists compute url-maps describe "${URLMAP}"; then
    ok "${URLMAP} exists — reimporting path matcher"
  else
    ${G} compute url-maps create "${URLMAP}" \
      --default-service="$(be_ref golf-data-projects ssa-landing-be)"
    ok "created ${URLMAP}"
  fi
  # Import full config so re-runs converge (path-matcher edits are additive
  # via import, matching how the sibling frontdoors are maintained by hand).
  tmp=$(mktemp)
  cat > "${tmp}" <<YAML
name: ${URLMAP}
defaultService: https://www.googleapis.com/compute/v1/$(be_ref golf-data-projects ssa-landing-be)
hostRules:
- hosts: [${APEX}, www.${APEX}]
  pathMatcher: apex
pathMatchers:
- name: apex
  defaultService: https://www.googleapis.com/compute/v1/$(be_ref golf-data-projects ssa-landing-be)
  pathRules:
  - paths: [/ncaaf, /ncaaf/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref ncaaf-data-projects ncaaf-dash-be)
  - paths: [/cfl, /cfl/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref cfl-data-projects cfl-dash-be)
  - paths: [/elomodel, /elomodel/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref nfl-data-projects nfl-elo-be)
  - paths: [/golf, /golf/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref golf-data-projects golf-dash-be)
  - paths: [/nhl, /nhl/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref nhl-data-projects nhl-dash-be)
  - paths: [/nba, /nba/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref nba-data-projects nba-dash-be)
  - paths: [/soccer, /soccer/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref soccer-data-projects soccer-hub-be)
  - paths: [/nfl, /nfl/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref nfl-data-projects nfl-mockdraft-be)
  - paths: [/nfl/elomodel, /nfl/elomodel/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref nfl-data-projects nfl-elo-be)
  - paths: [/epl, /epl/*]
    service: https://www.googleapis.com/compute/v1/$(be_ref soccer-data-projects soccer-epl-be)
YAML
  ${G} compute url-maps import "${URLMAP}" --source="${tmp}" --quiet
  rm -f "${tmp}"
  ok "path matcher imported (default→ssa-landing; /cfl /ncaaf /golf /nhl /nba /soccer /nfl(+/elomodel) /elomodel /epl)"
}

dns_auth() {
  log "DNS authorizations (cert can issue BEFORE DNS cutover)"
  for pair in apex-auth:${APEX} www-auth:www.${APEX}; do
    da="${pair%%:*}"; dom="${pair##*:}"
    if exists certificate-manager dns-authorizations describe "${da}"; then
      ok "${da} exists"
    else
      ${G} certificate-manager dns-authorizations create "${da}" --domain="${dom}"
      ok "created ${da} (${dom})"
    fi
  done
  echo
  echo "ADD THESE CNAME RECORDS AT SQUARESPACE (host → target):"
  ${G} certificate-manager dns-authorizations describe apex-auth \
    --format="value(dnsResourceRecord.name, dnsResourceRecord.data)"
  ${G} certificate-manager dns-authorizations describe www-auth \
    --format="value(dnsResourceRecord.name, dnsResourceRecord.data)"
}

cert() {
  log "Managed certificate + map"
  exists certificate-manager certificates describe "${CERT}" || \
    ${G} certificate-manager certificates create "${CERT}" \
      --domains="${APEX},www.${APEX}" --dns-authorizations="apex-auth,www-auth"
  exists certificate-manager maps describe "${CERT_MAP}" || \
    ${G} certificate-manager maps create "${CERT_MAP}"
  for dom in "${APEX}" "www.${APEX}"; do
    entry="entry-${dom//./-}"
    exists certificate-manager maps entries describe "${entry}" --map="${CERT_MAP}" || \
      ${G} certificate-manager maps entries create "${entry}" --map="${CERT_MAP}" \
        --hostname="${dom}" --certificates="${CERT}"
  done
  ${G} certificate-manager certificates describe "${CERT}" \
    --format="value(managed.state)"
}

frontend() {
  log "Global IP + proxies + forwarding rules (billing starts here)"
  exists compute addresses describe "${IP_NAME}" --global || \
    ${G} compute addresses create "${IP_NAME}" --global --ip-version=IPV4
  IP=$(${G} compute addresses describe "${IP_NAME}" --global --format="value(address)")
  ok "IP ${IP}"
  exists compute target-https-proxies describe "${HTTPS_PROXY}" || \
    ${G} compute target-https-proxies create "${HTTPS_PROXY}" \
      --url-map="${URLMAP}" \
      --certificate-map="${CERT_MAP}"
  exists compute forwarding-rules describe "${HTTPS_FR}" --global || \
    ${G} compute forwarding-rules create "${HTTPS_FR}" --global \
      --load-balancing-scheme=EXTERNAL_MANAGED \
      --address="${IP_NAME}" --target-https-proxy="${HTTPS_PROXY}" --ports=443
  ok "HTTPS frontend live on ${IP} (dark: DNS still points at domain mappings)"
}

status() {
  ${G} compute url-maps describe "${URLMAP}" --format="yaml(hostRules,pathMatchers)" 2>/dev/null | head -30
  ${G} certificate-manager certificates describe "${CERT}" --format="value(managed.state)" 2>/dev/null
  ${G} compute addresses describe "${IP_NAME}" --global --format="value(address)" 2>/dev/null
}

cmd="${1:-status}"; "${cmd//-/_}"
