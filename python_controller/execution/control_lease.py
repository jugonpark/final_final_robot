"""An exclusive wildcard bind shares the dashboard's UDP port as an OS lease."""
import socket


ESP32_COMMAND_PORT = 8888
ESP32_TELEMETRY_PORT = 8889


def open_control_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("", ESP32_TELEMETRY_PORT))
        sock.settimeout(0.02)
    except BaseException:
        sock.close()
        raise
    return sock
