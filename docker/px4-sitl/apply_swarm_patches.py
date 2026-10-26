#!/usr/bin/env python3
"""apply_swarm_patches.py <PX4_ROOT>
Пати SITL-роя по якорям (regex), а не по номерам строк. Якорь не найден или найден не там, где
ожидается — сборка образа падает. Повторный запуск безопасен (маркер 'swarm-sitl')."""
import pathlib
import re
import shutil
import sys

if len(sys.argv) != 2:
    sys.exit("usage: apply_swarm_patches.py <PX4_ROOT>")
ROOT = pathlib.Path(sys.argv[1]).resolve()
HERE = pathlib.Path(__file__).resolve().parent
MARK = "swarm-sitl"


def die(msg: str):
    sys.exit(f"PATCH FAILED: {msg}")


def need(p: pathlib.Path) -> pathlib.Path:
    if not p.exists():
        die(f"{p} не найден (другая версия PX4?)")
    return p


# ── P0. px_update_git_header: fix shallow NuttX git tags for SITL ────────────────────────────
header_py = ROOT / "src/lib/version/px_update_git_header.py"
if header_py.exists():
    t_hdr = header_py.read_text()
    t_hdr = t_hdr.replace("if (os.path.exists('platforms/nuttx/NuttX/nuttx/.git')):",
                          "if False and (os.path.exists('platforms/nuttx/NuttX/nuttx/.git')):")
    header_py.write_text(t_hdr)
nuttx_git = ROOT / "platforms/nuttx/NuttX/nuttx/.git"
if nuttx_git.exists():
    if nuttx_git.is_dir():
        shutil.rmtree(nuttx_git)
    else:
        nuttx_git.unlink()
print("P0 nuttx git header fix: ok")

# ── P1. airframe 4901_gz_x500_swarm ──────────────────────────────────────────────────────────
af_dir = need(ROOT / "ROMFS/px4fmu_common/init.d-posix/airframes")
shutil.copy(HERE / "airframes/4901_gz_x500_swarm", af_dir / "4901_gz_x500_swarm")
cmake = need(af_dir / "CMakeLists.txt")
txt = cmake.read_text()
if "4901_gz_x500_swarm" not in txt:
    txt, n = re.subn(r"^([ \t]*)4001_gz_x500[ \t]*$", r"\g<0>\n\g<1>4901_gz_x500_swarm", txt, count=1, flags=re.M)
    if n != 1:
        die("в airframes/CMakeLists.txt нет строки '4001_gz_x500' (куда регистрировать 4901?)")
    cmake.write_text(txt)
print("P1 airframe 4901: ok")

# ── P2. MAVLink onboard: адрес партнёра (-t) вместо 127.0.0.1 ────────────────────────────────
rc_mav = need(ROOT / "ROMFS/px4fmu_common/init.d-posix/px4-rc.mavlink")
t = rc_mav.read_text()
if MARK not in t:
    pat = re.compile(r"^([ \t]*mavlink start\b[^\n]*?-o \$udp_offboard_port_remote)([^\n]*)$", re.M)
    t, n = pat.subn(lambda m: f"{m.group(1)} -t ${{PX4_ONBOARD_HOST:-127.0.0.1}}{m.group(2)}  # {MARK}", t)
    if n != 1:
        die(f"px4-rc.mavlink: ожидалась ровно 1 строка 'mavlink start … -o $udp_offboard_port_remote', найдено {n}. "
            "Откройте файл и найдите onboard/offboard-линк на порт 14540+instance.")
    rc_mav.write_text(t)
print("P2 mavlink partner IP: ok")

# ── P3. uxrce_dds_client: хост агента, порт, namespace из окружения ──────────────────────────
DDS_LINE = ("uxrce_dds_client start -t udp -h ${PX4_XRCE_HOST:-127.0.0.1} "
            "-p ${PX4_XRCE_PORT:-8888} ${PX4_XRCE_NS:+-n ${PX4_XRCE_NS}}  # " + MARK)
dds_pat = re.compile(r"^([ \t]*)uxrce_dds_client[ \t]+start\b[^\n]*-t[ \t]+udp[^\n]*$", re.M)
candidates = list((ROOT / "ROMFS/px4fmu_common/init.d-posix").rglob("*")) + \
             list((ROOT / "ROMFS/px4fmu_common/init.d").glob("rc.*"))
dds_done = 0
for f in candidates:
    if not f.is_file():
        continue
    try:
        s = f.read_text()
    except UnicodeDecodeError:
        continue
    if "uxrce_dds_client" not in s:
        continue
    if MARK in s and DDS_LINE in s:
        dds_done += 1
        continue
    s2, n = dds_pat.subn(lambda m: m.group(1) + DDS_LINE, s)
    if n:
        f.write_text(s2)
        dds_done += n
        print(f"   dds line patched in {f.relative_to(ROOT)} ({n})")
if dds_done < 1:
    die("не найдена строка запуска 'uxrce_dds_client start -t udp …' (grep -rn uxrce_dds_client ROMFS/)")
print("P3 uxrce_dds_client: ok")

# ── P4. GPS simulation: инъекция глушения GPS (контракт — п. 2.5.2) ──────────────────────────────
gps_sim_cpp = ROOT / "src/modules/simulation/sensor_gps_sim/SensorGpsSim.cpp"
gz_bridge_cpp = ROOT / "src/modules/simulation/gz_bridge/GZBridge.cpp"

if gps_sim_cpp.exists():
    target_dir = gps_sim_cpp.parent
    cpp = gps_sim_cpp
elif gz_bridge_cpp.exists():
    target_dir = gz_bridge_cpp.parent
    cpp = gz_bridge_cpp
else:
    die("не найден ни SensorGpsSim.cpp, ни GZBridge.cpp для патча GPS")

shutil.copy(HERE / "params/gps_jam_params.c", target_dir / "gps_jam_params.c")   # имя обязано заканчиваться на params.c

msg_file = next((p for p in (ROOT / "msg/SensorGps.msg", ROOT / "msg/sensor_gps.msg") if p.exists()), None)
if msg_file is None:
    die("не найден msg/SensorGps.msg (sensor_gps.msg)")
has_jam_state = re.search(r"^\s*uint8\s+jamming_state\b", msg_file.read_text(), re.M) is not None
print(f"   sensor_gps: jamming_state {'есть' if has_jam_state else 'ОТСУТСТВУЕТ (поле пропускается)'}")

HELPER = r'''
// ---- swarm-sitl: инъекция глушения GPS (SIM_GPS_JAM / SIM_GPS_JAM_EPH) ----
#include <parameters/param.h>

static void swarm_apply_gps_jam(sensor_gps_s &g)
{
	static const param_t p_mode = param_find("SIM_GPS_JAM");
	static const param_t p_eph = param_find("SIM_GPS_JAM_EPH");
	int32_t mode = 0;
	float eph = 5.f;

	if (p_mode == PARAM_INVALID || param_get(p_mode, &mode) != 0 || mode == 0) {
		return;
	}

	if (p_eph != PARAM_INVALID) {
		param_get(p_eph, &eph);
	}

	if (mode == 1) {            // denied
		g.fix_type = 0;
		g.satellites_used = 0;
		g.eph = 9999.f;
		g.epv = 9999.f;
		g.s_variance_m_s = 100.f;
@@JAM3@@
	} else if (mode == 2) {     // degraded
		g.fix_type = 2;
		g.satellites_used = 4;
		g.eph = eph;
		g.epv = 1.5f * eph;
@@JAM2@@
	}
}
// ---- /swarm-sitl ----
'''
HELPER = HELPER.replace("@@JAM3@@", "\t\tg.jamming_state = 3;" if has_jam_state else "")
HELPER = HELPER.replace("@@JAM2@@", "\t\tg.jamming_state = 2;" if has_jam_state else "")

c = cpp.read_text()
if MARK not in c:
    inc = list(re.finditer(r"^#include[^\n]*$", c, re.M))
    if not inc:
        die(f"{cpp.name}: нет #include")
    pos = inc[-1].end()
    c = c[:pos] + "\n" + HELPER + c[pos:]
    call = re.compile(r"^([ \t]*)_sensor_gps_pub\.publish\(sensor_gps\);", re.M)
    if len(call.findall(c)) != 1:
        die(f"{cpp.name}: ожидался ровно один вызов '_sensor_gps_pub.publish(sensor_gps);'. "
            "Откройте файл и поправьте якорь.")
    c = call.sub(lambda m: f"{m.group(1)}swarm_apply_gps_jam(sensor_gps); // {MARK}\n{m.group(0)}", c)
    cpp.write_text(c)
print(f"P4 {cpp.stem} GPS jam: ok")

# ── P5. uxrce_dds_client: пересоздать сессию, если передача встала ────────────────────────────
# После перезапуска только агента (при живом PX4) новый агент отвечает на пинги (они работают и без сессии), поэтому клиент не считает
# себя отключённым, но старой сессии агент не знает: надёжный поток упирается в неподтверждённые пакеты, Payload tx = 0 B/s, борт не
# получает команд (живые прогоны 2026-10-26). Клиент всегда что-то публикует (vehicle_status и др. не реже 1 Гц), так что нулевая
# передача дольше 5 с — признак мёртвой сессии: разрываем её, клиент заново создаёт сессию с новым агентом.
dds_cpp = need(ROOT / "src/modules/uxrce_dds_client/uxrce_dds_client.cpp")
c = dds_cpp.read_text()
if MARK not in c:
    decl = re.compile(r"^([ \t]*)int poll_error_counter = 0;[ \t]*$", re.M)
    if len(decl.findall(c)) != 1:
        die("uxrce_dds_client.cpp: ожидалось одно объявление 'int poll_error_counter = 0;' перед циклом сессии")
    c = decl.sub(lambda m: f"{m.group(0)}\n{m.group(1)}hrt_abstime tx_stall_since = 0; // {MARK}", c)
    rates = re.compile(r"^([ \t]*)last_status_update = now;\n[ \t]*\}\n", re.M)
    if len(rates.findall(c)) != 1:
        die("uxrce_dds_client.cpp: ожидался один блок пересчёта _last_payload_tx_rate с 'last_status_update = now;'")

    def stall(m):
        ind = m.group(1)[:-1]          # отступ уровня цикла (блок if на табуляцию глубже)
        return (m.group(0) +
                f"{ind}// {MARK}: передача встала при «живых» пингах — сессия мертва, пересоздаём\n"
                f"{ind}if (_last_payload_tx_rate > 0) {{\n{ind}\ttx_stall_since = 0;\n\n"
                f"{ind}}} else if (tx_stall_since == 0) {{\n{ind}\ttx_stall_since = now;\n\n"
                f"{ind}}} else if (now - tx_stall_since > 5_s) {{\n"
                f"{ind}\tPX4_WARN(\"payload tx stalled for 5 s while the agent answers pings: recreating session\");\n"
                f"{ind}\t_connected = false;\n{ind}}}\n\n")
    c = rates.sub(stall, c)
    dds_cpp.write_text(c)
print("P5 uxrce_dds_client stalled-session reconnect: ok")
print("ALL PATCHES APPLIED")
