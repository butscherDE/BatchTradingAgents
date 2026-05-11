#!/bin/bash
# Creates downsampling buckets and tasks in InfluxDB.
# Run once after first boot of the monitoring stack.

INFLUX_URL="${INFLUX_URL:-http://localhost:8086}"
INFLUX_TOKEN="${INFLUX_TOKEN:-trading-metrics-token}"
INFLUX_ORG="${INFLUX_ORG:-trading}"

echo "Creating downsampling buckets..."

# 5-minute aggregates — 90 day retention
curl -s -X POST "$INFLUX_URL/api/v2/buckets" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"orgID\": \"$(curl -s "$INFLUX_URL/api/v2/orgs" -H "Authorization: Token $INFLUX_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)['orgs'][0]['id'])")\",
    \"name\": \"trading_metrics_5m\",
    \"retentionRules\": [{\"type\": \"expire\", \"everySeconds\": 7776000}]
  }"

echo ""

# 1-hour aggregates — 365 day retention
curl -s -X POST "$INFLUX_URL/api/v2/buckets" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"orgID\": \"$(curl -s "$INFLUX_URL/api/v2/orgs" -H "Authorization: Token $INFLUX_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)['orgs'][0]['id'])")\",
    \"name\": \"trading_metrics_1h\",
    \"retentionRules\": [{\"type\": \"expire\", \"everySeconds\": 31536000}]
  }"

echo ""
echo "Creating downsampling tasks..."

ORG_ID=$(curl -s "$INFLUX_URL/api/v2/orgs" -H "Authorization: Token $INFLUX_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)['orgs'][0]['id'])")

# 5-minute downsampling task
curl -s -X POST "$INFLUX_URL/api/v2/tasks" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"orgID\": \"$ORG_ID\",
    \"flux\": \"option task = {name: \\\"downsample_5m\\\", every: 5m}\\n\\nfrom(bucket: \\\"trading_metrics\\\")\\n  |> range(start: -10m)\\n  |> aggregateWindow(every: 5m, fn: sum, createEmpty: false)\\n  |> to(bucket: \\\"trading_metrics_5m\\\", org: \\\"trading\\\")\",
    \"status\": \"active\"
  }"

echo ""

# 1-hour downsampling task
curl -s -X POST "$INFLUX_URL/api/v2/tasks" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"orgID\": \"$ORG_ID\",
    \"flux\": \"option task = {name: \\\"downsample_1h\\\", every: 1h}\\n\\nfrom(bucket: \\\"trading_metrics_5m\\\")\\n  |> range(start: -2h)\\n  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false)\\n  |> to(bucket: \\\"trading_metrics_1h\\\", org: \\\"trading\\\")\",
    \"status\": \"active\"
  }"

echo ""
echo "Done. Downsampling tasks created."
