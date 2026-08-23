#!/bin/bash
set -euo pipefail

readonly LOG_FILE="${LOG_FILE:-/tmp/stealthy-auto-browse-sec.log}"
readonly SARIF_OUT="${SARIF_OUT:-sec.sarif}"
WORK_DIR="$(mktemp -d)"
readonly WORK_DIR
# shellcheck disable=SC2016
EMPTY_SARIF='{"version":"2.1.0","$schema":"https://json.schemastore.org/sarif-2.1.0.json","runs":[]}'
readonly EMPTY_SARIF
readonly SECURITY_TOOLS_PYTHON="${SECURITY_TOOLS_PYTHON:-/opt/sectools/bin/python}"
readonly SECURITY_TOOLS_SEMGREP="${SECURITY_TOOLS_SEMGREP:-/opt/sectools/bin/semgrep}"

log() {
	local level="$1"
	shift
	printf '{"time":"%s","level":"%s","file":"%s","line":%d,"func":"%s","msg":"%s"}\n' \
		"$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
		"$level" \
		"${BASH_SOURCE[1]##*/}" \
		"${BASH_LINENO[0]}" \
		"${FUNCNAME[1]:-main}" \
		"$*" >&2
}

cleanup() {
	rm -rf "$WORK_DIR"
}

on_error() {
	local status=$?
	log ERROR "command failed exit=$status"
	exit "$status"
}

trap cleanup EXIT
trap on_error ERR
exec > >(tee -a "$LOG_FILE") 2>&1

run_bandit() {
	local status

	set +e
	"$SECURITY_TOOLS_PYTHON" -m bandit -r app scripts -f sarif -o "$WORK_DIR/bandit.sarif" \
		>"$WORK_DIR/bandit.log" 2>&1
	status=$?
	set -e

	if [[ "$status" -ne 0 && "$status" -ne 1 ]]; then
		cat "$WORK_DIR/bandit.log" >&2
		log ERROR "bandit failed"
		return "$status"
	fi
}

run_semgrep() {
	local status

	set +e
	"$SECURITY_TOOLS_SEMGREP" scan \
		--config p/python \
		--config p/security-audit \
		--metrics=off \
		--sarif \
		--sarif-output "$WORK_DIR/semgrep.sarif" \
		app/*.py scripts/*.py >"$WORK_DIR/semgrep.log" 2>&1
	status=$?
	set -e

	if [[ "$status" -ne 0 && "$status" -ne 1 ]]; then
		cat "$WORK_DIR/semgrep.log" >&2
		log ERROR "semgrep failed"
		return "$status"
	fi
}

run_pip_audit() {
	local status

	set +e
	"$SECURITY_TOOLS_PYTHON" -m pip_audit \
		--path /usr/local/lib/python3.12/site-packages \
		--format json \
		--output "$WORK_DIR/pip-audit.json" \
		>"$WORK_DIR/pip-audit.log" 2>&1
	status=$?
	set -e

	if [[ "$status" -ne 0 && "$status" -ne 1 ]]; then
		cat "$WORK_DIR/pip-audit.log" >&2
		log ERROR "pip-audit failed"
		return "$status"
	fi
}

run_semgrep
run_bandit
run_pip_audit

if [[ -s "$WORK_DIR/pip-audit.json" ]]; then
	jq '{
        version: "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        runs: [{
            tool: {driver: {name: "pip-audit", rules: []}},
            results: [(.dependencies // [])[]? | .name as $name | .version as $version
                | (.vulns // [])[] | {
                    ruleId: .id,
                    level: "warning",
                    message: {text: ($name + " " + $version + ": " + (.description // .id))},
                    locations: [{physicalLocation: {
                        artifactLocation: {uri: "Dockerfile"},
                        region: {startLine: 1}
                    }}]
                }]
        }]
    }' "$WORK_DIR/pip-audit.json" >"$WORK_DIR/pip-audit.sarif"
fi

for scanner in semgrep bandit pip-audit; do
	[[ -s "$WORK_DIR/$scanner.sarif" ]] || printf '%s' "$EMPTY_SARIF" >"$WORK_DIR/$scanner.sarif"
done

jq -s '{
    version: "2.1.0",
    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    runs: (
        map(.runs // [])
        | add
        | map(.results |= map(select((.suppressions // []) | length == 0)))
    )
}' \
	"$WORK_DIR/semgrep.sarif" \
	"$WORK_DIR/bandit.sarif" \
	"$WORK_DIR/pip-audit.sarif" >"$SARIF_OUT"

count="$(jq '[.runs[].results[]?] | length' "$SARIF_OUT")"
log INFO "security scan wrote $count finding(s) to $SARIF_OUT"
