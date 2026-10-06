#!/usr/bin/env bash
# End-to-end test of the integration inside a real Home Assistant container,
# against a strict mock of Praktor's chat API (tests/mock_praktor.py).
#
#   tests/ha_e2e.sh            # uses HA_IMAGE (default: the stable image)
#
# Drives Home Assistant only through its REST API, as a user would: config
# flow (bad URL, bad token, good token), Assist conversation, the praktor.ask
# service, the options flow (number selector floats), error replies, and
# checks the token never shows up in HA's logs.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
HA_IMAGE=${HA_IMAGE:-ghcr.io/home-assistant/home-assistant:stable}
NET=praktor-ha-test
HA=praktor-ha-test-ha
MOCK=local-praktor # the hostname the App uses
PORT=${HA_TEST_PORT:-18123}
TOKEN=test-chat-token-$(openssl rand -hex 16)
API="http://127.0.0.1:$PORT"
START=$(date +%s)

log() { printf '[%4ss] %s\n' "$(($(date +%s) - START))" "$*"; }
pass() { log "PASS  $*"; }
fail() {
	log "FAIL  $*"
	docker logs $HA 2>&1 | tail -30 || true
	exit 1
}
cleanup() {
	docker rm -f $HA $MOCK >/dev/null 2>&1 || true
	docker network rm $NET >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

docker network create $NET >/dev/null
docker run -d --name $MOCK --network $NET -v "$ROOT/tests/mock_praktor.py:/mock.py:ro" \
	python:3.13-alpine python -u /mock.py "$TOKEN" >/dev/null
CFG=$(mktemp -d)
mkdir -p "$CFG/custom_components"
cp -R "$ROOT/custom_components/praktor" "$CFG/custom_components/"
docker run -d --name $HA --network $NET -p "127.0.0.1:$PORT:8123" -v "$CFG:/config" "$HA_IMAGE" >/dev/null
log "waiting for Home Assistant ($HA_IMAGE)"
for _ in $(seq 180); do
	curl -fsS "$API/api/onboarding" >/dev/null 2>&1 && break
	sleep 1
done
curl -fsS "$API/api/onboarding" >/dev/null || fail "Home Assistant did not start"

# Onboarding: create the owner and get an access token.
CLIENT="$API/"
code=$(curl -fsS -X POST "$API/api/onboarding/users" -H 'Content-Type: application/json' \
	-d "{\"client_id\":\"$CLIENT\",\"name\":\"Test\",\"username\":\"test\",\"password\":\"test-pass-123\",\"language\":\"en\"}" | jq -r .auth_code)
AT=$(curl -fsS -X POST "$API/auth/token" -d "grant_type=authorization_code&code=$code&client_id=$CLIENT" | jq -r .access_token)
[[ -n $AT && $AT != null ]] || fail "no access token"
H=(-H "Authorization: Bearer $AT" -H 'Content-Type: application/json')
pass "onboarded, got an access token"

flow() { curl -fsS -X POST "$API/api/config/config_entries/flow" "${H[@]}" -d '{"handler":"praktor"}' | jq -r .flow_id; }
submit() { curl -fsS -X POST "$API/api/config/config_entries/flow/$1" "${H[@]}" -d "$2"; }

# Config flow: invalid URL, wrong token, then the right one.
fid=$(flow)
[[ $fid != null ]] || fail "the integration did not load (no config flow)"
r=$(submit "$fid" '{"url":"ftp://local-praktor:8080","chat_token":"x"}')
[[ $(jq -r '.errors.url' <<<"$r") == invalid_url ]] || fail "ftp URL accepted: $r"
r=$(submit "$fid" "{\"url\":\"http://user:pw@$MOCK:8080\",\"chat_token\":\"x\"}")
[[ $(jq -r '.errors.url' <<<"$r") == invalid_url ]] || fail "URL with credentials accepted: $r"
r=$(submit "$fid" "{\"url\":\"http://$MOCK:8080/\",\"chat_token\":\"wrong-token\"}")
[[ $(jq -r '.errors.base' <<<"$r") == invalid_auth ]] || fail "wrong token not reported as invalid_auth: $r"
r=$(submit "$fid" "{\"url\":\"http://nowhere.invalid:8080\",\"chat_token\":\"$TOKEN\"}")
[[ $(jq -r '.errors.base' <<<"$r") == cannot_connect ]] || fail "unreachable host not reported as cannot_connect: $r"
pass "config flow rejects a bad URL, a wrong token and an unreachable host"
r=$(submit "$fid" "{\"url\":\"http://$MOCK:8080/\",\"chat_token\":\"$TOKEN\"}")
[[ $(jq -r .type <<<"$r") == create_entry ]] || fail "valid setup failed: $r"
ENTRY=$(jq -r .result.entry_id <<<"$r")
[[ $(jq -r .result.title <<<"$r") == "Praktor ($MOCK:8080)" ]] || fail "unexpected title: $r"
pass "config flow creates the entry (URL normalized, token validated via /api/chat only)"
fid=$(flow)
r=$(submit "$fid" "{\"url\":\"http://$MOCK:8080\",\"chat_token\":\"$TOKEN\"}")
[[ $(jq -r .reason <<<"$r") == already_configured ]] || fail "duplicate entry not refused: $r"
fid=$(flow)
r=$(submit "$fid" "{\"url\":\"HTTP://LOCAL-PRAKTOR:8080\",\"chat_token\":\"$TOKEN\"}")
[[ $(jq -r .reason <<<"$r") == already_configured ]] || fail "same URL in other case not refused: $r"
pass "a second entry for the same URL is refused (case-insensitive)"

sleep 2
EID=$(curl -fsS "$API/api/states" "${H[@]}" | jq -r '[.[] | select(.entity_id | startswith("conversation.praktor"))][0].entity_id')
[[ $EID == conversation.* ]] || fail "no conversation entity"
pass "conversation entity $EID"

converse() { curl -fsS -X POST "$API/api/conversation/process" "${H[@]}" -d "{\"text\":\"$1\",\"agent_id\":\"$EID\"}"; }
r=$(converse "hello there")
[[ $(jq -r .response.speech.plain.speech <<<"$r") == "pong: hello there" ]] || fail "Assist reply: $r"
pass "Assist gets the agent's reply"

r=$(curl -fsS -X POST "$API/api/services/praktor/ask?return_response" "${H[@]}" -d '{"message":"from a script"}')
[[ $(jq -r .service_response.reply <<<"$r") == "pong: from a script" && $(jq -r .service_response.agent <<<"$r") == claude ]] || fail "praktor.ask: $r"
pass "praktor.ask returns the reply as response data"

code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/api/services/praktor/ask?return_response" "${H[@]}" -d '{"message":"x","agent":"deepseek"}')
[[ $code == 500 || $code == 400 ]] || fail "praktor.ask to a forbidden agent returned $code"
hlog=$(docker logs $HA 2>&1)
grep -q "isn't allowed for the chat token" <<<"$hlog" || fail "forbidden agent not reported as such"
pass "praktor.ask to an agent the token can't reach fails with a clear message ($code)"

# Options flow: the number selector submits floats; Praktor needs integers.
ofid=$(curl -fsS -X POST "$API/api/config/config_entries/options/flow" "${H[@]}" -d "{\"handler\":\"$ENTRY\"}" | jq -r .flow_id)
r=$(curl -fsS -X POST "$API/api/config/config_entries/options/flow/$ofid" "${H[@]}" -d '{"agent":" claude ","timeout":30.0}')
[[ $(jq -r .type <<<"$r") == create_entry ]] || fail "options flow: $r"
sleep 2
r=$(converse "after options")
[[ $(jq -r .response.speech.plain.speech <<<"$r") == "pong: after options" ]] || fail "Assist after setting options (float timeout?): $r"
docker logs $MOCK 2>&1 | grep -q '"agent":"claude","timeout":30}' || fail "options not sent as agent=claude, integer timeout"
pass "options apply (agent trimmed, timeout sent as an integer)"

ofid=$(curl -fsS -X POST "$API/api/config/config_entries/options/flow" "${H[@]}" -d "{\"handler\":\"$ENTRY\"}" | jq -r .flow_id)
r=$(curl -fsS -X POST "$API/api/config/config_entries/options/flow/$ofid" "${H[@]}" -d '{}')
[[ $(jq -r .type <<<"$r") == create_entry ]] || fail "clearing options: $r"
sleep 2
converse "after clearing" >/dev/null
docker logs $MOCK 2>&1 | grep 'after clearing' | grep -q '"agent"' && fail "a cleared agent option was still sent"
pass "clearing the agent option goes back to Praktor's default agent"

r=$(converse "slow")
[[ $(jq -r .response.response_type <<<"$r") == error ]] || fail "timeout not reported as an error: $r"
[[ $(jq -r .response.speech.plain.speech <<<"$r") == *"timed out"* ]] || fail "timeout message: $r"
pass "a Praktor timeout becomes a clear Assist error"

# Nothing but POST /api/chat was ever called, and the token never leaked.
others=$(docker logs $MOCK 2>&1 | grep '^REQ' | grep -v '^REQ POST /api/chat ' || true)
[[ -z $others ]] || fail "unexpected requests to Praktor: $others"
pass "only POST /api/chat was called"
if docker logs $HA 2>&1 | grep -qF "$TOKEN" || grep -rqF "$TOKEN" "$CFG/home-assistant.log" 2>/dev/null; then
	fail "the chat token appears in Home Assistant's logs"
fi
pass "the chat token is not in Home Assistant's logs"
# Service errors raised on purpose above are logged by HA's REST API; only
# setup/load failures and unexpected exceptions count here.
if docker logs $HA 2>&1 | grep -E 'Error (setting up|loading).*praktor|Unexpected exception|Unexpected error' | grep -q .; then
	fail "errors in the Home Assistant log: $(docker logs $HA 2>&1 | grep -m3 -E 'Error (setting up|loading)|Unexpected')"
fi
pass "no setup errors or unexpected exceptions"
log "done"
