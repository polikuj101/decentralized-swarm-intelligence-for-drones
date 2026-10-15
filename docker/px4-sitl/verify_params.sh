#!/usr/bin/env bash
# verify_params.sh — проверка ключевых параметров в запущенном экземпляре. Запуск: docker compose exec fc-1 verify_params.sh
set -u
declare -A expect=(
  [MAV_SYS_ID]="${PX4_SYS_ID:-1}" [SIM_GPS_JAM]=0 [NAV_DLL_ACT]=0 [NAV_RCL_ACT]=0
  [COM_RC_IN_MODE]=4 [COM_RCL_EXCEPT]=7 [COM_ARM_WO_GPS]=1 [EKF2_HGT_REF]=0 [EKF2_EV_CTRL]=4
)
for p in SIM_GPS_JAM_EPH COM_OF_LOSS_T EKF2_EV_DELAY EKF2_EVV_NOISE EKF2_NOAID_TOUT EKF2_GPS_CTRL SDLOG_MODE; do
  expect[$p]=""   # достаточно существования
done
fail=0
for p in "${!expect[@]}"; do
  line=$(px4-shell param show "$p" 2>&1 | grep -m1 "$p" || true)
  if [ -z "$line" ]; then echo "MISSING $p"; fail=1; continue; fi
  want="${expect[$p]}"
  if [ -n "$want" ] && ! grep -Eq ":[[:space:]]*${want}([[:space:]]|$)" <<<"$line"; then
    echo "MISMATCH $p (want $want): $line"; fail=1
  else
    echo "ok       $p"
  fi
done
exit $fail
