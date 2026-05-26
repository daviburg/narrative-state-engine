#!/bin/bash
set -e

echo "=== Step 1: Create database ==="
sudo -u postgres createdb -O nse_orchestrator nse_orchestrator 2>&1 || echo "(DB may already exist)"

echo ""
echo "=== Step 2: Set password ==="
echo "ALTER USER nse_orchestrator WITH PASSWORD 'orchestrator_lan_2026';" | sudo -u postgres psql

echo ""
echo "=== Step 3: Get config paths ==="
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" | tr -d ' ')
PG_CONF=$(sudo -u postgres psql -t -c "SHOW config_file;" | tr -d ' ')
echo "pg_hba.conf: $PG_HBA"
echo "postgresql.conf: $PG_CONF"

echo ""
echo "=== Step 4: Add LAN access rule ==="
if grep -q "nse_orchestrator.*192.168.10" "$PG_HBA"; then
    echo "LAN rule already exists, skipping"
else
    echo "host    nse_orchestrator    nse_orchestrator    192.168.10.0/24    scram-sha-256" | sudo tee -a "$PG_HBA"
fi

echo ""
echo "=== Step 5: Set listen_addresses ==="
if grep -q "^listen_addresses" "$PG_CONF"; then
    echo "listen_addresses already set"
else
    sudo sed -i "s/#listen_addresses = .*/listen_addresses = '*'/" "$PG_CONF"
    echo "Set listen_addresses = '*'"
fi

echo ""
echo "=== Step 6: Restart PostgreSQL ==="
sudo systemctl restart postgresql
sudo systemctl status postgresql | head -5

echo ""
echo "=== Step 7: Verify local connection ==="
PGPASSWORD=orchestrator_lan_2026 psql -U nse_orchestrator -d nse_orchestrator -h localhost -c "SELECT 'DB_READY' as status;" 2>&1

echo ""
echo "=== Step 8: Add apt to nse-agent sudoers (already done but verify) ==="
cat /etc/sudoers.d/nse-agent

echo ""
echo "=== ALL DONE ==="
