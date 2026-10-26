# Пропатченная Mesa для simulator-core

Пакеты Mesa `23.2.1-1ubuntu3.1~22.04.4` из jammy-updates, пересобранные с `d3d12-cmd-signature-cache.patch` (версия `+swarm1`).
Без патча gz-sim теряет ~130 МБ/мин в `libd3d12.so` и драйвере NVIDIA, пока сенсоры рендерятся, и через ~1 ч его убивает OOM.
Подробности: `docs/gz_leak/REPORT.md`.

Здесь лежат только пакеты, которые уже стоят в образе: `libgl1-mesa-dri`, `libglx-mesa0`, `libegl-mesa0`, `libglapi-mesa`, `libgbm1`.
Dockerfile ставит их поверх штатных и держит (`apt-mark hold`), чтобы apt не вернул версию с утечкой.

Пересборка (в WSL, ~8 мин на 14 ядрах, ~6 ГБ во время сборки):

```bash
docker run --rm -v /var/tmp/mesabuild:/build -v "$PWD/docker/simulator-core/mesa:/patch:ro" ubuntu:22.04 bash -c '
  sed -i "s/^# deb-src/deb-src/" /etc/apt/sources.list && apt-get update &&
  apt-get install -y dpkg-dev devscripts fakeroot && apt-get build-dep -y mesa=23.2.1-1ubuntu3.1~22.04.4 &&
  cd /build && apt-get source mesa=23.2.1-1ubuntu3.1~22.04.4 && cd mesa-23.2.1 &&
  patch -p1 < /patch/d3d12-cmd-signature-cache.patch &&
  dch --local +swarm "d3d12: look up the command signature cache by key, not by &key" &&
  DEB_BUILD_OPTIONS="nocheck noautodbgsym parallel=14" dpkg-buildpackage -b -uc -us'
```

Если образ переедет на другую версию Mesa, патч надо применить к ней же: ошибка есть и в Mesa 26.2.5.
