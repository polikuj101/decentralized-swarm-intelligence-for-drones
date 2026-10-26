#!/usr/bin/env python3
"""gen_swarm.py — генерация x500_swarm_1..N, swarm_ground, swarm_world.sdf и scene.json.
Любое расхождение с ожидаемой структурой x500_base => завершение с ошибкой."""
import json
import os
import pathlib
import sys

from lxml import etree as ET

N = int(os.environ.get("SWARM_SIZE", "5"))
WORLD = os.environ.get("GZ_WORLD", "swarm_world")
SPACING = float(os.environ.get("SWARM_SPACING_M", "5.0"))
BASE = pathlib.Path(os.environ.get("PX4_GZ_DIR", "/opt/px4-gz")) / "models/x500_base/model.sdf"
OUT = pathlib.Path(os.environ.get("SWARM_OUT", "/opt/swarm/generated"))
SCENE_DIR = pathlib.Path(os.environ.get("SWARM_SCENE_DIR", "/opt/swarm/scene"))
LAT, LON, ELEV = 47.397971057728974, 8.546163739800146, 535.0   # как в стандартных мирах PX4

CAM = dict(w=320, h=240, hfov=1.2, rate=15)            # нижняя камера, моно 8 бит
RING = dict(samples=36, rmin=0.2, rmax=15.0, rate=10)  # кольцевой дальномер, шаг 10°
DOWN = dict(rmin=0.1, rmax=30.0, rate=20)              # нисходящий однолучевой дальномер

# (имя, x, y, размер x, y, z) — ENU, метры
OBSTACLES = [("obst_1", 25, 8, 3, 3, 6), ("obst_2", 30, -10, 4, 2, 5),
             ("obst_3", 40, 2, 2, 8, 4), ("obst_4", 18, -18, 3, 3, 7)]
# (имя, x, y, r, цвет RGB)
TARGETS = [("target_A", 40, 15, 1.5, (1, 0, 0)), ("target_B", 45, -12, 1.5, (0, 1, 0)),
           ("target_C", 20, 30, 1.5, (0, 0, 1))]


def die(msg: str):
    sys.exit(f"gen_swarm: {msg}")


def noise(std, bias_mean=None, bias_std=None):
    s = f'<noise type="gaussian"><mean>0</mean><stddev>{std}</stddev>'
    if bias_mean is not None:
        s += f'<bias_mean>{bias_mean}</bias_mean><bias_stddev>{bias_std}</bias_stddev>'
    return s + '</noise>'


def imu_xml():
    ang = "".join(f"<{a}>{noise(0.0003394, 3.88e-05, 3.88e-06)}</{a}>" for a in "xyz")
    lin = "".join(f"<{a}>{noise(0.004, 0.1, 0.001)}</{a}>" for a in "xyz")
    return ('<sensor name="imu_sensor" type="imu"><always_on>1</always_on><update_rate>250</update_rate>'
            f'<imu><angular_velocity>{ang}</angular_velocity><linear_acceleration>{lin}</linear_acceleration></imu></sensor>')


def navsat_xml(n):
    # В Gazebo NavSat шум horizontal задаётся в ГРАДУСАХ, а не в метрах:
    # 0.3 м -> 0.3 / 111320.0 град (~2.7e-6 град)
    h_noise_deg = 0.3 / 111320.0
    return (f'<sensor name="navsat_sensor" type="navsat"><always_on>1</always_on><update_rate>10</update_rate>'
            f'<topic>/swarm/{n}/navsat_raw</topic><navsat>'
            f'<position_sensing><horizontal>{noise(h_noise_deg)}</horizontal><vertical>{noise(0.5)}</vertical></position_sensing>'
            f'<velocity_sensing><horizontal>{noise(0.05)}</horizontal><vertical>{noise(0.05)}</vertical></velocity_sensing>'
            f'</navsat></sensor>')


def camera_xml(n):
    return (f'<sensor name="cam_down" type="camera"><pose>0 0 -0.05 0 1.5708 0</pose><always_on>1</always_on>'
            f'<update_rate>{CAM["rate"]}</update_rate><topic>/swarm/{n}/cam_down/image</topic>'
            f'<camera><horizontal_fov>{CAM["hfov"]}</horizontal_fov>'
            f'<image><width>{CAM["w"]}</width><height>{CAM["h"]}</height><format>L8</format></image>'
            f'<clip><near>0.1</near><far>100</far></clip>{noise(0.007)}</camera></sensor>')


def ring_xml(n):
    step = 6.283185307 / RING["samples"]
    amin, amax = -3.14159265, -3.14159265 + step * (RING["samples"] - 1)
    return (f'<sensor name="lidar_ring" type="gpu_lidar"><pose>0 0 0.08 0 0 0</pose><always_on>1</always_on>'
            f'<update_rate>{RING["rate"]}</update_rate><topic>/swarm/{n}/lidar_ring/scan</topic><lidar><scan>'
            f'<horizontal><samples>{RING["samples"]}</samples><resolution>1</resolution>'
            f'<min_angle>{amin:.6f}</min_angle><max_angle>{amax:.6f}</max_angle></horizontal>'
            f'<vertical><samples>1</samples><resolution>1</resolution><min_angle>0</min_angle><max_angle>0</max_angle></vertical>'
            f'</scan><range><min>{RING["rmin"]}</min><max>{RING["rmax"]}</max><resolution>0.01</resolution></range>'
            f'{noise(0.02)}</lidar></sensor>')


def down_xml(n):
    return (f'<sensor name="lidar_down" type="gpu_lidar"><pose>0 0 -0.06 0 1.5708 0</pose><always_on>1</always_on>'
            f'<update_rate>{DOWN["rate"]}</update_rate><topic>/swarm/{n}/lidar_down/scan</topic><lidar><scan>'
            f'<horizontal><samples>1</samples><resolution>1</resolution><min_angle>0</min_angle><max_angle>0</max_angle></horizontal>'
            f'<vertical><samples>1</samples><resolution>1</resolution><min_angle>0</min_angle><max_angle>0</max_angle></vertical>'
            f'</scan><range><min>{DOWN["rmin"]}</min><max>{DOWN["rmax"]}</max><resolution>0.01</resolution></range>'
            f'{noise(0.02)}</lidar></sensor>')


def motor_xml(num: str, direction: str) -> str:
    return (
        f'<plugin filename="gz-sim-multicopter-motor-model-system" name="gz::sim::systems::MulticopterMotorModel">'
        f'<jointName>rotor_{num}_joint</jointName>'
        f'<linkName>rotor_{num}</linkName>'
        f'<turningDirection>{direction}</turningDirection>'
        f'<timeConstantUp>0.0125</timeConstantUp>'
        f'<timeConstantDown>0.025</timeConstantDown>'
        f'<maxRotVelocity>1000.0</maxRotVelocity>'
        f'<motorConstant>8.54858e-06</motorConstant>'
        f'<momentConstant>0.016</momentConstant>'
        f'<commandSubTopic>command/motor_speed</commandSubTopic>'
        f'<motorNumber>{num}</motorNumber>'
        f'<rotorDragCoefficient>8.06428e-05</rotorDragCoefficient>'
        f'<rollingMomentCoefficient>1e-06</rollingMomentCoefficient>'
        f'<rotorVelocitySlowdownSim>10</rotorVelocitySlowdownSim>'
        f'<motorType>velocity</motorType>'
        f'</plugin>'
    )


def build_model(n: int):
    if not BASE.exists():
        die(f"{BASE} не найден")
    root = ET.parse(str(BASE)).getroot()
    model = root.find("model")
    if model is None:
        die("x500_base/model.sdf: нет <model>")
    model.set("name", f"x500_swarm_{n}")   # эквивалент переименования при spawn в px4-rc.gzsim
    base = model.find("link[@name='base_link']")
    if base is None:
        die("x500_base: нет link base_link")
    for name in ("imu_sensor", "navsat_sensor"):          # эти сенсоры определяем сами
        for s in [s for s in model.iter("sensor") if s.get("name") == name]:
            s.getparent().remove(s)
    for need in ("air_pressure_sensor",):   # эти берём из upstream
        if not any(s.get("name") == need for s in model.iter("sensor")):
            die(f"x500_base: нет сенсора {need} (изменилась структура модели PX4?)")
    for xml in (imu_xml(), navsat_xml(n), camera_xml(n)):
        base.append(ET.fromstring(xml))
    for num, direction in [("0", "ccw"), ("1", "ccw"), ("2", "cw"), ("3", "cw")]:
        model.append(ET.fromstring(motor_xml(num, direction)))
    d = OUT / "models" / f"x500_swarm_{n}"
    d.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(str(d / "model.sdf"), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    (d / "model.config").write_text(
        f'<?xml version="1.0"?><model><name>x500_swarm_{n}</name><version>1.0</version>'
        f'<sdf version="1.9">model.sdf</sdf><description>x500 + swarm sensors #{n}</description></model>')


def ground_texture(path: pathlib.Path):
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(7)
    size = 2048
    acc = np.zeros((size, size), dtype=np.float32)
    for k, w in ((8, 1.0), (16, 0.8), (32, 0.6), (64, 0.5), (128, 0.4), (256, 0.3), (512, 0.2)):
        layer = (rng.random((k, k)) * 255).astype(np.uint8)
        acc += w * np.asarray(Image.fromarray(layer).resize((size, size), Image.BICUBIC), dtype=np.float32)
    acc = (acc - acc.min()) / (acc.max() - acc.min())
    Image.fromarray((40 + acc * 175).astype(np.uint8)).save(str(path))


def build_ground():
    d = OUT / "models/swarm_ground"
    (d / "textures").mkdir(parents=True, exist_ok=True)
    ground_texture(d / "textures/ground.png")
    (d / "model.config").write_text('<?xml version="1.0"?><model><name>swarm_ground</name><version>1.0</version>'
                                    '<sdf version="1.9">model.sdf</sdf></model>')
    (d / "model.sdf").write_text(
        '<?xml version="1.0"?><sdf version="1.9"><model name="swarm_ground"><static>true</static><pose>25 0 0 0 0 0</pose>'
        '<link name="link"><collision name="c"><geometry><plane><normal>0 0 1</normal><size>300 300</size></plane></geometry></collision>'
        '<visual name="v"><geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry>'
        '<material><ambient>1 1 1 1</ambient><diffuse>1 1 1 1</diffuse><specular>0 0 0 1</specular>'
        '<pbr><metal><albedo_map>model://swarm_ground/textures/ground.png</albedo_map>'
        '<metalness>0</metalness><roughness>1</roughness></metal></pbr></material></visual></link></model></sdf>')


def static_box(name, x, y, sx, sy, sz):
    return (f'<model name="{name}"><static>true</static><pose>{x} {y} {sz / 2} 0 0 0</pose><link name="link">'
            f'<collision name="c"><geometry><box><size>{sx} {sy} {sz}</size></box></geometry></collision>'
            f'<visual name="v"><geometry><box><size>{sx} {sy} {sz}</size></box></geometry>'
            f'<material><ambient>0.45 0.45 0.5 1</ambient><diffuse>0.45 0.45 0.5 1</diffuse></material></visual></link></model>')


def static_pad(name, x, y, r, rgb):
    c = f"{rgb[0]} {rgb[1]} {rgb[2]} 1"
    return (f'<model name="{name}"><static>true</static><pose>{x} {y} 0.01 0 0 0</pose><link name="link">'
            f'<visual name="v"><geometry><cylinder><radius>{r}</radius><length>0.02</length></cylinder></geometry>'
            f'<material><ambient>{c}</ambient><diffuse>{c}</diffuse></material></visual></link></model>')


def build_world():
    drones = "\n".join(
        f'<include><uri>model://x500_swarm_{n}</uri><name>x500_swarm_{n}</name><pose>{(n - 1) * SPACING} 0 0 0 0 0</pose></include>'
        for n in range(1, N + 1))
    boxes = "\n".join(static_box(*o) for o in OBSTACLES)
    pads = "\n".join(static_pad(t[0], t[1], t[2], t[3], t[4]) for t in TARGETS)
    sdf = f'''<?xml version="1.0"?>
<sdf version="1.9">
<world name="{WORLD}">
  <physics type="ode"><max_step_size>0.004</max_step_size><real_time_factor>1.0</real_time_factor><real_time_update_rate>250</real_time_update_rate></physics>
  <gravity>0 0 -9.8066</gravity>
  <magnetic_field>6.0e-06 2.3e-05 -4.2e-05</magnetic_field>
  <atmosphere type="adiabatic"/>
  <scene><grid>false</grid><ambient>0.8 0.8 0.8 1</ambient><background>0.7 0.8 0.9 1</background><shadows>false</shadows></scene>
  <spherical_coordinates>
    <surface_model>EARTH_WGS84</surface_model><world_frame_orientation>ENU</world_frame_orientation>
    <latitude_deg>{LAT}</latitude_deg><longitude_deg>{LON}</longitude_deg><elevation>{ELEV}</elevation><heading_deg>0</heading_deg>
  </spherical_coordinates>
  <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
  <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
  <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
  <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"/>
  <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
  <plugin filename="gz-sim-air-pressure-system" name="gz::sim::systems::AirPressure"/>
  <plugin filename="gz-sim-magnetometer-system" name="gz::sim::systems::Magnetometer"/>
  <plugin filename="gz-sim-navsat-system" name="gz::sim::systems::NavSat"/>
  <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
  <light name="sun" type="directional"><cast_shadows>false</cast_shadows><pose>0 0 100 0 0 0</pose>
    <diffuse>0.9 0.9 0.9 1</diffuse><specular>0.2 0.2 0.2 1</specular><direction>-0.5 0.1 -0.9</direction></light>
  <include><uri>model://swarm_ground</uri></include>
  {boxes}
  {pads}
  {drones}
</world>
</sdf>'''
    (OUT / "swarm_world.sdf").write_text(sdf)


def build_scene():
    scene = {
        "world": WORLD, "swarm_size": N,
        "origin": {"lat_deg": LAT, "lon_deg": LON, "elevation_m": ELEV, "frame": "ENU"},
        "drones": [{"id": n, "model": f"x500_swarm_{n}", "x_m": (n - 1) * SPACING, "y_m": 0.0} for n in range(1, N + 1)],
        "obstacles": [dict(name=o[0], x_m=o[1], y_m=o[2], size_m=[o[3], o[4], o[5]]) for o in OBSTACLES],
        "targets": [dict(name=t[0], x_m=t[1], y_m=t[2], radius_m=t[3]) for t in TARGETS],
        "sensors": {"cam_down": CAM, "lidar_ring": RING, "lidar_down": DOWN},
    }
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    (SCENE_DIR / "scene.json").write_text(json.dumps(scene, indent=2))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for i in range(1, N + 1):
        build_model(i)
    build_ground()
    build_world()
    build_scene()
    print(f"gen_swarm: ok, N={N}, world={WORLD}")
