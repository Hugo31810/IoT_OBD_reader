#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import csv
import random
import threading
from datetime import datetime

import serial
import paho.mqtt.client as mqtt

try:
    import can
except ImportError:
    can = None

try:
    import pynmea2
except ImportError:
    pynmea2 = None


MODO_REAL = False

BROKER = "broker.emqx.io"
PORT = 1883
TOPIC = "coches/raspi1/telemetria"
CAN_INTERFACE = "can0"

GPS_PORT = "/dev/serial0"
GPS_BAUDRATE = 9600

gps_data = {"lat": 0.0, "lon": 0.0, "sats": 0}
gps_running = True

last_data = {
    "rpm": 800,
    "speed": 0,
    "temp": 80,
    "oil": 80,
    "fuel": 50,
    "voltage": 12.5,
    "load": 10,
    "map": 100,
    "iat": 25,
    "throttle": 0,
    "lambda": 1.0,
    "timing": 10,
    "lat": 0.0,
    "lon": 0.0,
    "sats": 0
}


def read_gps():
    global gps_data, gps_running

    if not pynmea2:
        print("GPS desactivado (pynmea2 no disponible)")
        return

    try:
        ser = serial.Serial(GPS_PORT, GPS_BAUDRATE, timeout=1)
        print("Puerto GPS abierto")
    except Exception as e:
        print(f"No se pudo abrir GPS: {e}")
        return

    while gps_running:
        try:
            line = ser.readline().decode(errors="replace").strip()
            if line.startswith(("$GPGGA", "$GNGGA")):
                msg = pynmea2.parse(line)
                if msg.gps_qual > 0:
                    gps_data["lat"] = round(msg.latitude, 6)
                    gps_data["lon"] = round(msg.longitude, 6)
                    gps_data["sats"] = int(msg.num_sats)
        except Exception:
            continue

    ser.close()


def setup_can():
    os.system("sudo ip link set can0 down")
    time.sleep(0.5)
    os.system("sudo ip link set can0 up type can bitrate 500000")
    time.sleep(0.5)


def create_csv():
    path = "/home/pi/logs"
    os.makedirs(path, exist_ok=True)

    name = f"telemetria_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    filename = os.path.join(path, name)

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Timestamp", "RPM", "Speed", "Temp", "Oil", "MAP", "IAT",
            "Throttle", "Fuel", "Volts", "Lambda", "Timing", "Load",
            "Lat", "Lon", "Sats"
        ])

    return filename


def save_csv(filename, data):
    try:
        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%H:%M:%S.%f")[:-3],
                data["rpm"], data["speed"], data["temp"], data["oil"],
                data["map"], data["iat"], data["throttle"], data["fuel"],
                data["voltage"], data["lambda"], data["timing"], data["load"],
                data["lat"], data["lon"], data["sats"]
            ])
    except Exception:
        pass


def send_pid(bus, pid):
    try:
        msg = can.Message(
            arbitration_id=0x7DF,
            data=[0x02, 0x01, pid, 0, 0, 0, 0, 0],
            is_extended_id=False
        )
        bus.send(msg)

        start = time.time()
        while time.time() - start < 0.5:
            r = bus.recv(0.01)
            if r and r.arbitration_id == 0x7E8 and r.data[2] == pid:
                return r.data
    except Exception:
        return None


def parse_pid(pid, data):
    if not data:
        return None

    if pid == 0x0C:
        return int(((data[3] << 8) + data[4]) / 4)
    if pid == 0x0D:
        return int(data[3])
    if pid == 0x11:
        return round(data[3] * 100 / 255, 1)
    if pid == 0x2F:
        return round(data[3] * 100 / 255, 1)
    if pid == 0x42:
        return round(((data[3] << 8) + data[4]) / 1000, 1)
    if pid in (0x05, 0x5C, 0x0F):
        return int(data[3] - 40)
    if pid == 0x04:
        return round(data[3] * 100 / 255, 1)
    if pid == 0x0B:
        return int(data[3])
    if pid == 0x0E:
        return round(data[3] / 2 - 64, 1)
    if pid == 0x44:
        return round(((data[3] << 8) + data[4]) / 32768, 3)

    return None


def read_obd(bus):
    global last_data

    pids = {
        "rpm": 0x0C,
        "speed": 0x0D,
        "temp": 0x05,
        "throttle": 0x11,
        "map": 0x0B,
        "oil": 0x5C,
        "iat": 0x0F,
        "timing": 0x0E,
        "lambda": 0x44,
        "fuel": 0x2F,
        "voltage": 0x42,
        "load": 0x04,
    }

    data = last_data.copy()

    for k, pid in pids.items():
        val = parse_pid(pid, send_pid(bus, pid))
        if val is not None:
            data[k] = val
        time.sleep(0.02)

    data.update(gps_data)
    last_data = data.copy()
    return data


def simulated_data():
    global last_data

    data = {
        "rpm": random.randint(2500, 4500),
        "speed": random.randint(80, 120),
        "temp": random.randint(85, 98),
        "oil": random.randint(90, 110),
        "fuel": random.randint(0, 60),
        "voltage": round(random.uniform(13.8, 14.2), 1),
        "load": random.randint(40, 60),
        "map": random.randint(90, 150),
        "iat": random.randint(30, 45),
        "throttle": random.randint(30, 70),
        "lambda": round(random.uniform(0.95, 1.0), 3),
        "timing": round(random.uniform(15, 25), 1),
        **gps_data
    }

    last_data = data.copy()
    return data


def connect_mqtt():
    client = mqtt.Client()
    while True:
        try:
            client.connect(BROKER, PORT, 60)
            client.loop_start()
            return client
        except Exception:
            time.sleep(5)


def main():
    global gps_running

    if pynmea2:
        t = threading.Thread(target=read_gps, daemon=True)
        t.start()

    bus = None
    if MODO_REAL and can:
        setup_can()
        bus = can.interface.Bus(channel=CAN_INTERFACE, bustype="socketcan")

    csv_file = create_csv()
    mqtt_client = connect_mqtt()

    try:
        while True:
            data = read_obd(bus) if MODO_REAL else simulated_data()
            mqtt_client.publish(TOPIC, json.dumps(data))
            save_csv(csv_file, data)
            time.sleep(1)
    except KeyboardInterrupt:
        gps_running = False
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
