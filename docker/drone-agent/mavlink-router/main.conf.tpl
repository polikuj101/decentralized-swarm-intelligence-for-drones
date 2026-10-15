[General]
TcpServerPort = 0
ReportStats = false

[UdpEndpoint fc]
Mode = Server
Address = 0.0.0.0
Port = ${MAVLINK_ONBOARD_PORT}

[UdpEndpoint gcs]
Mode = Normal
Address = 172.32.0.2
Port = 14550

[UdpEndpoint ew]
Mode = Server
Address = 127.0.0.1
Port = 14600
