/**
 * Swarm SITL: GPS jamming mode injected by gz_bridge
 *
 * @min 0
 * @max 2
 * @value 0 Off
 * @value 1 Denied (fix_type = 0)
 * @value 2 Degraded (fix_type = 2, eph from SIM_GPS_JAM_EPH)
 * @group Simulator
 */
PARAM_DEFINE_INT32(SIM_GPS_JAM, 0);

/**
 * Swarm SITL: reported horizontal accuracy in degraded GPS mode
 *
 * @unit m
 * @min 0.5
 * @max 10000
 * @decimal 1
 * @group Simulator
 */
PARAM_DEFINE_FLOAT(SIM_GPS_JAM_EPH, 5.0f);
