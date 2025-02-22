import asyncio
import smbus2
import paho.mqtt.client as mqtt
import random
import string
import uuid
import json
import socket

bus = smbus2.SMBus(1)

# ตั้งค่า Address ของ PCF8574
PCF8574_RELAY_1 = 0x20
PCF8574_RELAY_2 = 0x21
PCF8574_SWITCH_1 = 0x23
PCF8574_SWITCH_2 = 0x22

# ตั้งค่ารีเลย์ให้ปิด
bus.write_byte(PCF8574_RELAY_1, 0xFF)
bus.write_byte(PCF8574_RELAY_2, 0xFF)

# ฟังก์ชันสำหรับแสดงและคืนค่า IP Address
def get_wifi_ip():
    try:
        ip_address = socket.gethostbyname(socket.gethostname())
        return ip_address
    except Exception as e:
        return "172.20.10.7"

# ฟังก์ชันสำหรับสร้าง Token
def generate_token():
    mac = uuid.getnode()
    mac_address = ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
    random.seed(mac_address)
    return ''.join(random.choices(string.ascii_letters + string.digits, k=len(mac_address)))

# ฟังก์ชันสำหรับแปลงเลขฐานสิบเป็นเลขฐานสอง (True = Normal, False = Inverted)
def convert_number(n, state):
    return (1 << (n - 1)) & 0xFF if state else ~ (1 << (n - 1)) & 0xFF

# ฟังก์ชันสำหรับเขียนข้อมูลไปยัง PCF8574
def write_pcf8574(address, value):
    bus.write_byte(address, value)

# ฟังก์ชันสำหรับอ่านสถานะสวิตช์
def read_switch_input(pcf8574_address):
    return bus.read_byte(pcf8574_address)

# ฟังก์ชันสำหรับตั้งค่าสถานะรีเลย์
relay_lock = asyncio.Lock()
RELAY_STATES = {PCF8574_RELAY_1: 0xFF, PCF8574_RELAY_2: 0xFF}

# ฟังก์ชันสำหรับเปิด/ปิดรีเลย์
async def set_relay_pin(pcf8574_address, compartment, state):
    binary = convert_number(compartment, state)
    RELAY_STATES[pcf8574_address] = RELAY_STATES[pcf8574_address] ^ binary if state else ~(RELAY_STATES[pcf8574_address] ^ binary) & 0xFF
    print(f"Compartmen:{compartment} {'Closing' if state else 'Opening'}")
    async with relay_lock:
        write_pcf8574(pcf8574_address, RELAY_STATES[pcf8574_address])

# ฟังก์ชันสำหรับตรวจสอบช่องเก็บของที่พร้อมใช้งาน
def compartment_ready(switch1_state, switch2_state):
    return ",".join(
        [str(compartment_num + 1) for compartment_num in range(8) if not (switch1_state & (1 << compartment_num))]
        + [str(compartment_num + 9) for compartment_num in range(8) if not (switch2_state & (1 << compartment_num))]
    )

# ตั้งค่าตัวแปรสำหรับ MQTT
MQTT_HOST = get_wifi_ip()
MQTT_PORT = 1883
MQTT_CLIENT = None
TOKEN = generate_token()
TOPIC_COMPARTMENT = {"borrow": [], "return": [], "compartments": []}
STATUS_SWITCH = {"switch1": [1] * 8, "switch2": [1] * 8}
RELAY_THREAD = {"THREAD": asyncio.Queue(), "TASK": None}
SWITCH_THREAD = {"THREAD": None, "COMPARTMENTS": []}

# ฟังก์ชันสำหรับสร้างข้อมูลของช่องเก็บของ
def compartments_data(client):
    TOPIC_COMPARTMENT["compartments"] = []
    TOPIC_COMPARTMENT["borrow"] = []
    TOPIC_COMPARTMENT["return"] = []

    for compartment in compartment_ready(read_switch_input(PCF8574_SWITCH_1), read_switch_input(PCF8574_SWITCH_2)).split(","):
        if compartment > "0" and compartment < "17":
            STATUS_SWITCH["switch1"][int(compartment) - 1] = 0 if int(compartment) <= 8 else 1
            STATUS_SWITCH["switch2"][int(compartment) - 9] = 0 if int(compartment) > 8 else 1
            TOPIC_COMPARTMENT["compartments"].append(int(compartment))
            TOPIC_COMPARTMENT["borrow"].append(f"{TOKEN}/borrow/{compartment}/open")
            TOPIC_COMPARTMENT["return"].append(f"{TOKEN}/return/{compartment}/open")
            client.subscribe(f"{TOKEN}/borrow/{compartment}/open")
            client.subscribe(f"{TOKEN}/return/{compartment}/open")
        else:
            continue

    print(f"Status Compartment Switch1: {STATUS_SWITCH['switch1']}")
    print(f"Status Compartment Switch2: {STATUS_SWITCH['switch2']}")
    print(f"Borrow Compartments: {TOPIC_COMPARTMENT['borrow']}")
    print(f"Return Compartments: {TOPIC_COMPARTMENT['return']}")
    print(f"Compartments_compartments: {TOPIC_COMPARTMENT['compartments']}")

# ฟังก์ชันสำหรับเชื่อมต่อ MQTT
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅:MQTT Connected...")
        compartments_data(client)
        client.subscribe("request/locker")
        client.subscribe(f"{TOKEN}/check")
        client.subscribe(f"{TOKEN}/check/compartment")
    else:
        print(f"❌:Connection failed with code {rc}")

# ฟังก์ชันสำหรับรับข้อความจาก MQTT
def on_message(client, userdata, msg):
    message = msg.payload.decode()
    topic = msg.topic
    print(f"Received topic: {topic}")

    if topic == f"{TOKEN}/check":
        client.publish(f"{TOKEN}/check/respond", "ACK")
    
    if topic == f"{TOKEN}/check/compartment":
        CHECK_COMPARTMENTS = {"Compartment": compartment_ready(read_switch_input(PCF8574_SWITCH_1), read_switch_input(PCF8574_SWITCH_2))}
        client.publish(f"{TOKEN}/check/compartment/respond", json.dumps(CHECK_COMPARTMENTS))
        compartments_data(client)
        print(f"CHECK_COMPARTMENTS: {CHECK_COMPARTMENTS}")

    if topic == "request/locker":
        TOPIC_SEND = {"Token": TOKEN, "Compartment": compartment_ready(read_switch_input(PCF8574_SWITCH_1), read_switch_input(PCF8574_SWITCH_2))}
        client.publish("respond/locker", json.dumps(TOPIC_SEND))
        compartments_data(client)
        print(f"TOPIC_SEND: {TOPIC_SEND}")

    if topic in TOPIC_COMPARTMENT["borrow"] or topic in TOPIC_COMPARTMENT["return"]:
        compartment_num = int(topic.split("/")[2].replace("Compartment", ""))
        relay_address = PCF8574_RELAY_1 if compartment_num <= 8 else PCF8574_RELAY_2
        if RELAY_THREAD["TASK"] is None:
            RELAY_THREAD["TASK"] = loop.call_soon_threadsafe(asyncio.create_task, relay_thread(RELAY_THREAD["THREAD"]))
            SWITCH_THREAD["THREAD"] = loop.create_task(switch_thread())
        RELAY_THREAD["THREAD"].put_nowait((relay_address, compartment_num))

# ฟังก์ชันสำหรับเปิด/ปิดรีเลย์
async def relay_thread(queue):
    while not queue.empty():
        address, pin= await queue.get()
        await set_relay_pin(address, pin, False)
        await asyncio.sleep(500 / 1000)
        await set_relay_pin(address, pin, True)
        await asyncio.sleep(3000 / 1000)
        queue.task_done()
    print("Relay thread stopped")
    RELAY_THREAD["TASK"] = None

# ฟังก์ชันตรวจสอบสถานะสวิตช์
async def check_switch_state(client, switch_state, status_compartment, offset=0):
    for compartment_num in range(1 + offset, 9 + offset):
        compartment_pin = compartment_num - 1 - offset
        compartment_switch_status = (switch_state >> compartment_pin) & 1

        if compartment_num not in TOPIC_COMPARTMENT["compartments"]:
            continue

        if status_compartment[compartment_pin] == 0 and compartment_switch_status == 1:
            SWITCH_THREAD["COMPARTMENTS"].append(compartment_num)
            print(f"Switch: {bin(switch_state)} Compartment{compartment_num}/OPEN")
            client.publish(f"{TOKEN}/borrow/{compartment_num}/status", "OPEN")

        if status_compartment[compartment_pin] == 1 and compartment_switch_status == 0:
            SWITCH_THREAD["COMPARTMENTS"].remove(compartment_num)
            print(f"Switch: {bin(switch_state)} Compartment{compartment_num}/CLOSE")
            client.publish(f"{TOKEN}/borrow/{compartment_num}/status", "CLOSE")
        status_compartment[compartment_pin] = compartment_switch_status

    print(f"Status Compartment Switch1: {STATUS_SWITCH['switch1']}")
    print(f"Status Compartment Switch2: {STATUS_SWITCH['switch2']}")

# ฟังก์ชันสำหรับสร้าง MQTT Client
def mqtt_thread():
    global MQTT_CLIENT
    MQTT_CLIENT = mqtt.Client()
    MQTT_CLIENT.on_connect = on_connect
    MQTT_CLIENT.on_message = on_message

    try:
        MQTT_CLIENT.connect(MQTT_HOST, MQTT_PORT, 60)
        MQTT_CLIENT.loop_start()
    except Exception as e:
        print(f"Could not connect to MQTT Broker: {e}")

# สร้างฟังก์ชันสำหรับควบคุมรีเลย์
async def switch_thread():
    try:
        previous_switch1_state = previous_switch2_state = None
        while True:
            switch1_state = read_switch_input(PCF8574_SWITCH_1)
            switch2_state = read_switch_input(PCF8574_SWITCH_2)
            if previous_switch1_state is not None and previous_switch1_state != switch1_state:
                await check_switch_state(MQTT_CLIENT, switch1_state, STATUS_SWITCH["switch1"])
            if previous_switch2_state is not None and previous_switch2_state != switch2_state:
                await check_switch_state(MQTT_CLIENT, switch2_state, STATUS_SWITCH["switch2"], offset=8)
            previous_switch1_state = switch1_state
            previous_switch2_state = switch2_state
            await asyncio.sleep(0.1)

            if not SWITCH_THREAD["COMPARTMENTS"]:
                loop.call_soon_threadsafe(SWITCH_THREAD["THREAD"].cancel)
                break

    except asyncio.CancelledError:
        SWITCH_THREAD["THREAD"] = None
        print("Switch thread stopped")

# Main Entry
if __name__ == "__main__":
    mqtt_thread()
    loop = asyncio.get_event_loop()
    loop.run_forever()
