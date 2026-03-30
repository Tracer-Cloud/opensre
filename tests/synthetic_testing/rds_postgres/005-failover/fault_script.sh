#!/usr/bin/env bash
# Force a Multi-AZ failover by rebooting the primary with failover.
aws rds reboot-db-instance --db-instance-identifier payments-prod --force-failover
